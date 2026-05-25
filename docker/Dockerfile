FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    cron \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/run_daily.sh

# Install crontab
COPY docker/crontab /etc/cron.d/patent-scrapers
RUN chmod 0644 /etc/cron.d/patent-scrapers && \
    crontab /etc/cron.d/patent-scrapers

# Create log directory
RUN mkdir -p /app/logs /app/data

CMD ["/bin/sh", "-c", "printenv | grep -v '^no_proxy' > /etc/environment && exec cron -f"]
