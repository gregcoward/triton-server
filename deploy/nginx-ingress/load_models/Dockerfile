FROM ubuntu:latest
COPY fetch_models.sh /fetch_models.sh
RUN chmod +x /fetch_models.sh
RUN apt-get update && apt-get -y install wget
CMD ["/fetch_models.sh"]