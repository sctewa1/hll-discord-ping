FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Australia/Sydney \
    CONFIG_PATH=/app/config.jsonc \
    PYTHONPATH=/app

WORKDIR /app

# System deps for tz + certs
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

# App code + config
COPY . .
# optional: ensure the logger path exists if your logging_config writes there
RUN mkdir -p /logs

# Run the Django management command (no --config arg)
CMD ["python","-u","manage.py","send_ping"]
