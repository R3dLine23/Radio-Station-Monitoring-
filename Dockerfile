FROM python:3.12-slim
WORKDIR /app
COPY radio_monitor ./radio_monitor
# Mount your config at /app/config.toml (see docker-compose.yml).
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "radio_monitor", "--config", "/app/config.toml"]
