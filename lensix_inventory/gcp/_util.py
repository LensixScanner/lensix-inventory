"""Small helpers shared by every GCP gather module, kept in one place
instead of copy-pasted into each one.
"""


def extract_resource_name(value):
    """Normalize any GCP resource reference field (bare name, partial
    resource path, or full selfLink URL — the same three-shape ambiguity
    documented across nearly every Compute Engine reference field, e.g.
    Firewall.network, BackendService.network, TargetHttpsProxy.sslPolicy)
    down to its short name. Generic — not specific to networks despite
    extract_network_name's own name (kept for the many existing call
    sites already reading network/subnetwork fields specifically)."""
    if not value or not isinstance(value, str):
        return None
    return value.rsplit('/', 1)[-1]


def extract_network_name(value):
    """Normalize a VPC network reference (bare name or full selfLink URL) to
    its short name."""
    return extract_resource_name(value)


def normalize_location_to_region(location):
    """A Container/Cloud SQL/etc. resource's own `location` field can be
    either a REGION ('us-central1', for regional resources) or a ZONE
    ('us-central1-a', for zonal ones) — GCP zone names are always
    <region>-<single lowercase letter>, a naming convention with no
    documented exception, so stripping a trailing '-<letter>' segment
    reliably recovers the region either way. Returns `location` unchanged
    if it doesn't match that zone shape (already a region, or some other
    value entirely)."""
    if not location or not isinstance(location, str):
        return location
    prefix, sep, suffix = location.rpartition('-')
    if sep and len(suffix) == 1 and suffix.isalpha():
        return prefix
    return location


def extract_subnet_region(value):
    """Pull the region out of a subnetwork reference (full selfLink,
    'projects/p/regions/r/subnetworks/s', or the shortest documented
    partial form 'regions/r/subnetworks/s' — confirmed against the real
    discovery document schema, all of which always include the region
    segment). None if the value doesn't contain a recognizable
    'regions/<r>' segment at all."""
    if not value or not isinstance(value, str):
        return None
    parts = value.rstrip('/').split('/')
    if 'regions' in parts:
        idx = parts.index('regions')
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None
