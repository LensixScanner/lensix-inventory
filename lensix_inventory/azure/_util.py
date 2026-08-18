"""Small helpers shared by every Azure gather module, kept in one place
instead of copy-pasted into each one.
"""

import re

_RESOURCE_GROUP_RE = re.compile(r'/resourceGroups/([^/]+)', re.IGNORECASE)


def resource_group(resource_id):
    """Best-effort resource group extraction from an ARM resource ID path."""
    if not resource_id:
        return None
    m = _RESOURCE_GROUP_RE.search(resource_id)
    return m.group(1) if m else None


def as_dict(obj):
    """`obj.as_dict()`, falling back to a minimal id/name dict if the SDK
    model doesn't support serialization for some reason."""
    if obj is None or isinstance(obj, dict):
        return obj
    try:
        return obj.as_dict()
    except Exception:
        return {'id': getattr(obj, 'id', None), 'name': getattr(obj, 'name', None)}
