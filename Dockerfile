FROM python:3.12-slim
WORKDIR /app
COPY hubnet /app/hubnet
ENV HUBNET_DIR=/data PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["python3", "-m", "hubnet.server"]
