#!/bin/bash
ls /models/
if [ -d "/models/model_repository" ]; then
    echo "Models are present."
else
    echo "Models are not present."
fi