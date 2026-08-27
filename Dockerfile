FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-aws.txt requirements-azure.txt requirements-gcp.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY lensix_inventory/ lensix_inventory/
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

RUN useradd --no-create-home --uid 1000 lensix \
    && chown -R lensix:lensix /app
USER lensix

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
