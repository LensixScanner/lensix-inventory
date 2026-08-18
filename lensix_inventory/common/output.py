"""Inventory file writer — gzip-compressed NDJSON, one manifest line
followed by one resource record per line.

Format (see README.md for the full spec):
    line 1:   {"kind": "manifest", ...}
    line 2+:  {"kind": "resource", "resource_type": ..., "region": ...,
               "resource_id": ..., "resource_name": ..., "scope_id": ...,
               "raw": {...}, "secret_scan_hits": [...]}

NDJSON (not a single JSON array) so the import side can stream-process line
by line instead of materializing the whole file in memory, and so one
malformed record doesn't invalidate the entire upload.

`resource_type`/`region`/`resource_id`/`resource_name`/`scope_id` match
Lensix's `resources` table columns exactly, so an import handler can write
these straight through with no field mapping. `raw` carries the full
(already-JSON-safe) API response data the check functions need.
`secret_scan_hits` is present only for resource types where this tool
redacts a raw secret-bearing field client-side (see common/secrets.py) —
its presence signals "the raw field was intentionally omitted here, this
is the local scan result instead."
"""

import gzip
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

FORMAT_VERSION = "1.0"

# Substrings that show up in permission-denied errors across all three
# providers' SDKs (AWS botocore, Azure SDK, GCP googleapiclient). Used to
# separate "you're missing a read permission" (expected, common, doesn't
# need fixing before uploading) from "something else went wrong" (worth a
# closer look) in the end-of-run summary. Deliberately over-inclusive —
# false positives here just mean an unusual error gets the reassuring
# framing, which is the safer default for a customer-run tool.
_PERMISSION_ERROR_RE = re.compile(
    r'AccessDenied|UnauthorizedOperation|AuthorizationFailed|AuthorizationError'
    r'|Forbidden|PERMISSION_DENIED|not authorized|is not authorized'
    r'|does not have authorization|InsufficientAccountPermissions'
    r'|\b403\b',
    re.IGNORECASE,
)


def is_permission_error(message):
    return bool(_PERMISSION_ERROR_RE.search(str(message)))


def _json_default(value):
    # boto3/SDK responses commonly embed datetime (and occasionally other
    # non-JSON-native types); render datetimes as ISO 8601, everything else
    # as its string form rather than failing the whole write.
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class InventoryWriter:
    """Collects resource records in memory, then writes the whole file in
    one pass so the manifest (which needs final counts) can be line 1."""

    def __init__(self, provider, account_id, tool_version):
        self.provider = provider
        self.account_id = account_id
        self.tool_version = tool_version
        self._records = []
        self._counts = defaultdict(int)
        self._regions = set()
        self._errors = []

    def add_resource(self, resource_type, region, resource_id, resource_name,
                      raw, scope_id=None, secret_scan_hits=None):
        record = {
            "kind": "resource",
            "resource_type": resource_type,
            "region": region,
            "resource_id": resource_id,
            "resource_name": resource_name,
            "raw": raw,
        }
        if scope_id is not None:
            record["scope_id"] = scope_id
        if secret_scan_hits is not None:
            record["secret_scan_hits"] = secret_scan_hits
        self._records.append(record)
        self._counts[resource_type] += 1
        if region:
            self._regions.add(region)

    def add_error(self, region, source, message):
        # Gathering errors (e.g. AccessDenied on one API call) shouldn't
        # abort the whole run — record them so the customer (and Lensix,
        # after import) can see what was skipped instead of crashing the
        # scan.
        self._errors.append({"region": region, "source": source, "message": str(message)})

    @property
    def errors(self):
        return list(self._errors)

    @property
    def resource_counts(self):
        return dict(self._counts)

    def write(self, path):
        manifest = {
            "kind": "manifest",
            "format_version": FORMAT_VERSION,
            "tool_version": self.tool_version,
            "provider": self.provider,
            "account_id": self.account_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regions": sorted(self._regions),
            "resource_counts": dict(self._counts),
            "total_resources": len(self._records),
            "error_count": len(self._errors),
        }
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(json.dumps(manifest, default=_json_default) + "\n")
            for record in self._records:
                f.write(json.dumps(record, default=_json_default) + "\n")
            for error in self._errors:
                f.write(json.dumps({"kind": "error", **error}, default=_json_default) + "\n")
        # This file holds a full resource-configuration inventory (security
        # group rules, IAM policies, and the rest) — not raw secrets (see
        # common/secrets.py), but still sensitive enough that it shouldn't
        # default to the process umask's usual world-readable permissions
        # on a shared/multi-user machine. os.chmod (not a mode= arg to
        # gzip.open, which doesn't take one) is the only way to set this
        # after the fact; skipped quietly on platforms where chmod is a
        # no-op (Windows) rather than failing the whole write over it.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return manifest
