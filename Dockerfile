FROM python:3.11-slim

WORKDIR /app

# gosu für sauberen Privilegienabbau im Entrypoint
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Nicht-root-Benutzer; /app/data wird zur Laufzeit im Entrypoint übereignet
RUN useradd -r -u 10001 appuser \
    && mkdir -p /app/data/uploads /app/data/branding \
    && chown -R appuser:appuser /app

EXPOSE 5000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--preload", "app:create_app()"]
