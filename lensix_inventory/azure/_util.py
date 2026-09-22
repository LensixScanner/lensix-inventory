"""Small helpers shared by every Azure gather module, kept in one place
instead of copy-pasted into each one.
"""

import re

_RESOURCE_GROUP_RE = re.compile(r'/resourceGroups/([^/]+)', re.IGNORECASE)
_RESOURCE_GROUP_SCOPE_RE = re.compile(r'^/subscriptions/[^/]+/resourceGroups/[^/]+$', re.IGNORECASE)


def resource_group(resource_id):
    """Best-effort resource group extraction from an ARM resource ID path."""
    if not resource_id:
        return None
    m = _RESOURCE_GROUP_RE.search(resource_id)
    return m.group(1) if m else None


def is_resource_group_scope(scope):
    """True when `scope` is EXACTLY a resource-group-level ARM path
    (`/subscriptions/{sub}/resourceGroups/{rg}`, no further segments) —
    used by policy.py and activitylog.py to derive a *_group (applies_to)
    edge only for a scope field whose value actually names a
    resource_group resource_id, not a subscription-level or a
    resource-level scope (see either module's own docstring for the full
    reasoning behind skipping those two cases)."""
    return bool(scope) and bool(_RESOURCE_GROUP_SCOPE_RE.match(scope))


def normalize_id(resource_id):
    """Lowercased ARM resource ID, for case-insensitive comparison — Azure
    resource IDs are case-insensitive by ARM's own semantics, but
    different APIs can echo back different casing for the identical
    resource (established precedent already in this codebase: rsv.py's
    own get_protected_vm_resource_ids() and vm.py's own protected_vm_ids/
    scheduled_vmss_ids sets, both pre-dating resource_edges support here).
    Used to build {normalize_id(target.id): target.id} resolution maps so
    an edge's to_id always matches the target resource's own actual
    resource_id string (whatever casing IT happens to use), never the
    referencing field's own casing, which may differ."""
    return (resource_id or '').lower()


def as_dict(obj):
    """`obj.as_dict()`, falling back to a minimal id/name dict if the SDK
    model doesn't support serialization for some reason."""
    if obj is None or isinstance(obj, dict):
        return obj
    try:
        return obj.as_dict()
    except Exception:
        return {'id': getattr(obj, 'id', None), 'name': getattr(obj, 'name', None)}
