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


# ── Tag-based suppression ────────────────────────────────────────────────────
#
# A customer can suppress a resource, or specific checks on it, entirely from
# their own cloud account — no Lensix UI action needed — by tagging it:
#   lensix-suppress = true                     -> the resource is excluded
#                                                  from inventory entirely
#                                                  (see add_resource() below;
#                                                  it's never even recorded).
#   lensix-suppress-checks = <check-ids>       -> the resource IS still
#                                                  inventoried, but the listed
#                                                  checks are skipped for it
#                                                  (see
#                                                  raw['_SuppressedCheckIds'],
#                                                  read by every scanner-light
#                                                  check-evaluation loop).
#
# One key convention across all three providers, not a per-provider special
# case — chosen to be valid everywhere at once: GCP label keys/values are
# restricted to lowercase letters, digits, underscores, and hyphens (no
# colons, no uppercase, 63-char value limit) — the tightest of the three
# clouds' rules, so it's the one this has to satisfy. check_ids are already
# snake_case and never contain a hyphen themselves, which is exactly why
# lensix-suppress-checks joins multiple check_ids with '-' rather than the
# more obvious ',' — GCP label values can't contain a comma at all.
#
#   AWS (EC2-family): raw['Tags'] -> [{'Key': ..., 'Value': ...}, ...]
#   AWS (ECS/EKS/Glue/MSK/others): raw['tags'] -> [{'key': ..., 'value': ...}, ...]
#                                  (lowercase field names — a genuine
#                                  AWS-internal inconsistency between
#                                  services, not a typo)
#   Azure: raw['tags']   -> {key: value}
#   GCP:   raw['labels'] -> {key: value}
# add_resource()'s own `tags` argument accepts any of these shapes
# directly (see _normalize_tags below) — each gather() call site passes
# whichever its own raw record actually carries, unmodified.

SUPPRESS_TAG_KEY = 'lensix-suppress'
SUPPRESS_CHECKS_TAG_KEY = 'lensix-suppress-checks'


def _normalize_tags(tags):
    """dict form of a resource's tags, regardless of which of the real-
    world shapes they arrived in: AWS EC2-family's list of
    {'Key','Value'} dicts, a handful of newer/non-EC2 AWS services'
    list of lowercase {'key','value'} dicts instead (ECS, EKS, Glue, MSK,
    and others — a genuine AWS-internal inconsistency, not a typo; each
    gather call site passes its own API's raw Tag list through
    unmodified rather than reshaping it itself), KMS's own list of
    {'TagKey','TagValue'} dicts (yet another AWS-internal variant), or
    Azure/GCP's already-flat {key: value} dict. None/empty input (a
    resource with no tags/labels at all, or one an older gather call site
    hasn't been updated to pass tags= for yet) returns {}."""
    if not tags:
        return {}
    if isinstance(tags, dict):
        return tags

    def _key(t):
        for field in ('Key', 'key', 'TagKey'):
            if t.get(field) is not None:
                return t[field]
        return None

    def _value(t):
        for field in ('Value', 'value', 'TagValue'):
            if field in t:
                return t[field]
        return None

    return {_key(t): _value(t) for t in tags if _key(t) is not None}


def parse_tag_suppression(tags):
    """(full_suppress: bool, suppressed_check_ids: frozenset[str]) for a
    resource's own tags/labels — the single source of truth both
    add_resource() (below) and lensix-scanner-light's own suppressions-
    table sync read from, so the two can't drift on what a tag actually
    means. full_suppress=True makes suppressed_check_ids moot (the whole
    resource is gone), but both are always returned for callers that want
    to record intent even when full suppression already covers it."""
    normalized = _normalize_tags(tags)
    full_suppress = str(normalized.get(SUPPRESS_TAG_KEY, '')).strip().lower() == 'true'
    checks_value = normalized.get(SUPPRESS_CHECKS_TAG_KEY, '') or ''
    suppressed_check_ids = frozenset(
        check_id for check_id in (c.strip() for c in checks_value.split('-')) if check_id
    )
    return full_suppress, suppressed_check_ids


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
        self._tag_suppressions = []

    def add_resource(self, resource_type, region, resource_id, resource_name,
                      raw, scope_id=None, secret_scan_hits=None, tags=None):
        """tags: this resource's own tags/labels, in whichever of the two
        real shapes the caller already has them in (AWS's list of
        {'Key','Value'} dicts, or Azure/GCP's flat {key: value} dict) —
        see this module's own "Tag-based suppression" section above for
        the full convention. Omit (or pass None) for a resource type this
        provider's tagging doesn't apply to; nothing about suppression
        applies to it then, same as before tags= existed.

        A resource tagged lensix-suppress=true is NOT recorded at all —
        it never becomes a `records` entry, so it never reaches
        persist_resources()/findings evaluation/anything downstream; the
        "should not be sent to Lensix" the tag promises is literal. A
        resource tagged lensix-suppress-checks=<ids> IS recorded
        normally, with raw['_SuppressedCheckIds'] injected so every
        scanner-light check-evaluation loop can skip just those checks
        for it. Either way, the intent is also recorded in
        tag_suppressions (below) for lensix-scanner-light's own
        suppressions-table sync — a separate, visibility-only step; this
        method's own enforcement above doesn't depend on that sync
        happening or succeeding."""
        full_suppress, suppressed_check_ids = parse_tag_suppression(tags)
        if full_suppress or suppressed_check_ids:
            self._tag_suppressions.append({
                "resource_type": resource_type,
                "resource_id": resource_id,
                "region": region,
                "full_suppress": full_suppress,
                "check_ids": sorted(suppressed_check_ids),
            })
        if full_suppress:
            return

        if suppressed_check_ids:
            raw = dict(raw) if isinstance(raw, dict) else raw
            if isinstance(raw, dict):
                # A sorted list, not the frozenset itself: this raw dict
                # gets JSON-serialized verbatim by write() for the
                # upload path, and json.dumps has no native frozenset
                # support — _json_default's str(value) fallback would
                # otherwise mangle it into a single unusable string like
                # "frozenset({'ec2_deletion_protection'})" instead of a
                # real JSON array. A plain list works identically for
                # every `in` check/iteration this value is used for
                # (see ec2_import.py's per-resource _active()/
                # eni_suppressed_check_ids usage) on both the live path
                # (never serialized) and the upload path (always is).
                raw['_SuppressedCheckIds'] = sorted(suppressed_check_ids)

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

    @property
    def tag_suppressions(self):
        """Every resource whose own tags requested suppression (full or
        per-check), gathered so far — including ones NOT in `records`,
        since a fully-suppressed resource is deliberately excluded from
        there. lensix-scanner-light's own suppressions-table sync reads
        this to keep the app's existing Suppressions UI showing tag-
        derived suppressions too, alongside manually-created ones."""
        return list(self._tag_suppressions)

    @property
    def records(self):
        """The resource records gathered so far, as plain dicts — same
        shape each line 2+ of the written file has (minus the "kind" key),
        available before (and without requiring) a call to write(). Lets a
        caller consume gathered data directly in memory — build a writer,
        call the gather_fn(s) it needs, and read .records straight away
        instead of writing to (and re-parsing) a file."""
        return list(self._records)

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
