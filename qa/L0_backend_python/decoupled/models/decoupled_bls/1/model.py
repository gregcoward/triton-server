# Copyright (c) 2022, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#  * Neither the name of NVIDIA CORPORATION nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import triton_python_backend_utils as pb_utils
import json
import threading
import time
import numpy as np
import asyncio


class TritonPythonModel:
    """ This model sends an error message with the first request.
    """

    def initialize(self, args):
        # You must parse model_config. JSON string is not parsed here
        self.model_config = model_config = json.loads(args['model_config'])

        using_decoupled = pb_utils.using_decoupled_model_transaction_policy(
            model_config)
        if not using_decoupled:
            raise pb_utils.TritonModelException(
                """the model `{}` can generate any number of responses per request,
                enable decoupled transaction policy in model configuration to
                serve this model""".format(args['model_name']))

        # Get OUT configuration
        out_config = pb_utils.get_output_config_by_name(model_config, "OUT")

        # Convert Triton types to numpy types
        self.out_dtype = pb_utils.triton_string_to_numpy(
            out_config['data_type'])

        self.inflight_thread_count = 0
        self.inflight_thread_count_lck = threading.Lock()

    async def execute(self, requests):
        """ This function is called on inference request.
        """

        # Only generate the error for the first request
        for i, request in enumerate(requests):
            request_input = pb_utils.get_input_tensor_by_name(request, 'IN')

            # Sync BLS request
            infer_request = pb_utils.InferenceRequest(
                model_name='identity_fp32',
                requested_output_names=["OUTPUT0"],
                inputs=[pb_utils.Tensor('INPUT0', request_input.as_numpy())])
            infer_response = infer_request.exec()
            if infer_response.has_error():
                raise pb_utils.TritonModelException(
                    f"BLS Response has an error: {infer_response.error().message()}"
                )

            output0 = pb_utils.get_output_tensor_by_name(
                infer_response, "OUTPUT0")
            if np.any(output0.as_numpy() != request_input.as_numpy()):
                raise pb_utils.TritonModelException(
                    f"BLS Request input and BLS response output do not match. {request_input.as_numpy()} != {output0.as_numpy()}"
                )

            # Async BLS Request
            infer_response = await infer_request.async_exec()
            output0 = pb_utils.get_output_tensor_by_name(
                infer_response, "OUTPUT0")
            if np.any(output0.as_numpy() != request_input.as_numpy()):
                raise pb_utils.TritonModelException(
                    f"BLS Request input and BLS response output do not match. {request_input.as_numpy()} != {output0.as_numpy()}"
                )

            thread1 = threading.Thread(target=self.response_thread,
                                       args=(request.get_response_sender(),
                                             pb_utils.get_input_tensor_by_name(
                                                 request, 'IN').as_numpy()))
            thread2 = threading.Thread(target=asyncio.run,
                                       args=(self.response_thread_async(
                                           request.get_response_sender(),
                                           pb_utils.get_input_tensor_by_name(
                                               request, 'IN').as_numpy()),))

            thread1.daemon = True
            thread2.daemon = True

            with self.inflight_thread_count_lck:
                self.inflight_thread_count += 2

            thread1.start()
            thread2.start()

        return None

    def response_thread(self, response_sender, in_input):
        # The response_sender is used to send response(s) associated with the
        # corresponding request.

        in_value = in_input

        infer_request = pb_utils.InferenceRequest(
            model_name='identity_fp32',
            requested_output_names=["OUTPUT0"],
            inputs=[pb_utils.Tensor('INPUT0', in_input)])
        infer_response = infer_request.exec()
        output0 = pb_utils.get_output_tensor_by_name(infer_response, "OUTPUT0")
        if infer_response.has_error():
            response = pb_utils.InferenceResponse(
                error=infer_response.error().message())
            response_sender.send(response)
        elif np.any(in_input != output0.as_numpy()):
            error_message = (
                "BLS Request input and BLS response output do not match."
                f" {in_value} != {output0.as_numpy()}")
            response = pb_utils.InferenceResponse(error=error_message)
            response_sender.send(response)
        else:
            output_tensors = [pb_utils.Tensor('OUT', in_value)]
            response = pb_utils.InferenceResponse(output_tensors=output_tensors)
            response_sender.send(response)

        with self.inflight_thread_count_lck:
            self.inflight_thread_count -= 1

    async def response_thread_async(self, response_sender, in_input):
        # The response_sender is used to send response(s) associated with the
        # corresponding request.

        # Sleep 5 seconds to make sure the main thread has returned.
        time.sleep(5)

        in_value = in_input

        infer_request = pb_utils.InferenceRequest(
            model_name='identity_fp32',
            requested_output_names=["OUTPUT0"],
            inputs=[pb_utils.Tensor('INPUT0', in_input)])
        infer_response = await infer_request.async_exec()
        output0 = pb_utils.get_output_tensor_by_name(infer_response, "OUTPUT0")
        if infer_response.has_error():
            response = pb_utils.InferenceResponse(
                error=infer_response.error().message())
            response_sender.send(response)
        elif np.any(in_input != output0.as_numpy()):
            error_message = (
                "BLS Request input and BLS response output do not match."
                f" {in_value} != {output0.as_numpy()}")
            response = pb_utils.InferenceResponse(error=error_message)
            response_sender.send(response)
        else:
            output_tensors = [pb_utils.Tensor('OUT', in_value)]
            response = pb_utils.InferenceResponse(output_tensors=output_tensors)
            response_sender.send(response)

        # This thread will close the response sender because it is exepcted to
        # take longer.
        response_sender.close()

        with self.inflight_thread_count_lck:
            self.inflight_thread_count -= 1

    def finalize(self):
        """`finalize` is called only once when the model is being unloaded.
        Implementing `finalize` function is OPTIONAL. This function allows
        the model to perform any necessary clean ups before exit.
        """
        print('Finalize invoked')

        inflight_threads = True
        while inflight_threads:
            with self.inflight_thread_count_lck:
                inflight_threads = (self.inflight_thread_count != 0)
            if inflight_threads:
                time.sleep(0.1)

        print('Finalize complete...')