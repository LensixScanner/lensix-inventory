#!/usr/bin/env bash
# Gathers an inventory (python -m lensix_inventory, never run.sh — that's
# the interactive/venv-setup wrapper meant for a human running this by
# hand) and uploads the result to Lensix via a Bearer token scoped to one
# account.
#
# LENSIX_UPLOAD_TOKEN is the only thing that must be passed in explicitly —
# it's a Lensix-specific secret with no other source to fall back to.
# LENSIX_API_URL defaults to the real Lensix API if not given. Credentials
# for the cloud provider itself are never handled here at all — same as
# when this tool is run locally, whatever the AWS/Azure/GCP SDK's default
# credential chain resolves inside the container is what gets used (env
# vars, `aws configure`'s profile file, an attached IAM
# role/managed identity/workload identity, ...). lensix_inventory itself
# validates that chain with one cheap, real API call per provider before
# it starts gathering, so a bad/missing credential fails fast with a clear
# message instead of surfacing as a wall of per-module errors.
#
# LENSIX_REGIONS (AWS only) is read directly by lensix_inventory itself
# (see cli.py) — it's just inherited from this container's environment,
# nothing to wire up here.
set -uo pipefail

: "${LENSIX_PROVIDER:?LENSIX_PROVIDER is required (aws|azure|gcp)}"
: "${LENSIX_UPLOAD_TOKEN:?LENSIX_UPLOAD_TOKEN is required}"
LENSIX_API_URL="${LENSIX_API_URL:-https://lensix.com}"

case "$LENSIX_PROVIDER" in
  aws|azure|gcp) ;;
  *) echo "error: LENSIX_PROVIDER must be aws, azure, or gcp" >&2; exit 1 ;;
esac

OUTPUT="/tmp/lensix-inventory-${LENSIX_PROVIDER}.ndjson.gz"

echo "==> Gathering $LENSIX_PROVIDER inventory ..."
python -m lensix_inventory --provider "$LENSIX_PROVIDER" --output "$OUTPUT"
GATHER_EXIT=$?
if [[ $GATHER_EXIT -ne 0 ]]; then
  echo "error: gathering failed (exit $GATHER_EXIT) — not uploading a partial/missing file." >&2
  exit "$GATHER_EXIT"
fi

echo "==> Uploading to $LENSIX_API_URL ..."
VERSION="$(python -c 'from lensix_inventory import __version__; print(__version__)' 2>/dev/null || echo unknown)"
HTTP_STATUS=$(curl -sS -o /tmp/upload-response.json -D /tmp/upload-response-headers.txt -w '%{http_code}' \
  -X POST \
  -A "lensix-inventory/$VERSION" \
  -H "Authorization: Bearer $LENSIX_UPLOAD_TOKEN" \
  -F "file=@${OUTPUT}" \
  "${LENSIX_API_URL%/}/api/inventory/upload")

if [[ "$HTTP_STATUS" -lt 200 || "$HTTP_STATUS" -ge 300 ]]; then
  echo "error: upload failed (HTTP $HTTP_STATUS)" >&2
  # A non-JSON body here (an HTML page, most often) means something in
  # front of the app — a CDN/WAF, a proxy — intercepted the request before
  # it ever reached Lensix; dumping the raw HTML is more noise than signal,
  # so give a pointed hint instead and only show the body for a real
  # (parseable) API error response.
  if python -c "import json; json.load(open('/tmp/upload-response.json'))" 2>/dev/null; then
    cat /tmp/upload-response.json >&2
  else
    echo "(response wasn't from the Lensix API — looks like it was blocked or redirected" >&2
    echo " before reaching it, e.g. by a CDN/WAF in front of $LENSIX_API_URL. Check that" >&2
    echo " LENSIX_API_URL is correct and that this host/User-Agent isn't being blocked.)" >&2
    # Cloudflare sets CF-RAY on every response it proxies, blocked or not —
    # reading it from the response header (not scraping the block page's
    # HTML, which can change) gives whoever manages that Cloudflare zone
    # the one thing they need to look up exactly what rule fired and why.
    CF_RAY="$(grep -i '^cf-ray:' /tmp/upload-response-headers.txt 2>/dev/null | tr -d '\r' | cut -d' ' -f2)"
    if [[ -n "$CF_RAY" ]]; then
      echo "Cloudflare Ray ID: $CF_RAY — share this with whoever manages Cloudflare for" >&2
      echo "${LENSIX_API_URL#*://} to look up exactly what rule blocked this request." >&2
    fi
  fi
  exit 1
fi

echo "==> Upload succeeded."
cat /tmp/upload-response.json
rm -f "$OUTPUT"
