# Runs lensix_inventory unattended: gather an inventory, then upload it to
# Lensix using a token scoped to one account (see
# lensix-web-light's POST /api/accounts/[id]/upload-token for how a paid
# customer generates that token, and POST /api/inventory/upload for what
# receives it here). Intended to be run on the customer's own schedule
# (cron, a k8s CronJob, ...) — this repo does not orchestrate that itself.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-aws.txt requirements-azure.txt requirements-gcp.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY lensix_inventory/ lensix_inventory/
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Runs as an unprivileged user rather than the image's default root — this
# container only ever reads cloud APIs and writes to /app and /tmp (both
# owned by this user below), so root privileges buy it nothing and standard
# container hardening says not to run as root when nothing requires it.
RUN useradd --no-create-home --uid 1000 lensix \
    && chown -R lensix:lensix /app
USER lensix

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
