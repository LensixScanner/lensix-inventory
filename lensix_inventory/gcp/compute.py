"""Compute Engine gathering — VM instances, custom images, disk snapshots,
managed instance groups (with their matching autoscaler config merged in,
mirroring how aws/sg.py merges rules into each group's raw record), and
project-level metadata.

Only the data-fetching calls are included here (aggregatedList over
instances/instance-group-managers/autoscalers, images/snapshots list,
getIamPolicy for images, and the project-level commonInstanceMetadata get)
— default-service-account use, public IP, IP forwarding, serial port, OS
Login (+2FA), disk encryption, Shielded VM settings, auto-restart/live-
migration, auto-delete disk, deletion protection, image/public-IAM, old
snapshots, MIG autoscale/auto-heal/single-zone, and project-wide SSH keys/
OS Login evaluation is left server-side.

Secrets exception (matching aws/ec2.py's user-data handling): instance and
project metadata VALUES are exactly the kind of free-text field that can
carry hardcoded credentials. Those values are scanned locally for secrets
and discarded immediately; only the metadata KEY names and the scan result
travel in the uploaded record, never the values themselves — with one
narrow exception (`_CHECK_RELEVANT_METADATA_KEYS`): serial-port-enable,
enable-oslogin, enable-oslogin-2fa, ssh-keys, and created-by are known,
non-secret config values (ssh-keys holds public keys, not secrets — public
by definition; created-by is the launching Managed Instance Group's own
resource path, not customer data) that several checks — and, for
created-by, the scanner-light noise-reduction grouping that collapses a
MIG's instances into one row — need the actual value of, not just "is
this key present." Their values are still scanned for secrets like every
other key (in case a customer stashed something unexpected in one), but
also kept verbatim in `itemValues`, so those checks can read them without
a second live call.

GCP resources are project-scoped, not per-region like AWS — every list call
below is an aggregatedList/list(project=...) call that already covers every
zone in the project in one paginated request, so gather() takes no region
argument (unlike aws/ec2.py's gather(region, writer)); each resource's own
zone is derived from its own `zone`/`selfLink` field.

Nothing here is Cloud-Monitoring-metrics-based (CPU-based right-sizing,
idle instance detection) — everything is static resource/config listing,
so there's nothing time-windowed to skip.
"""

from googleapiclient import discovery

from . import _util
from ..common.secrets import scan_text_for_secrets


def _zone_from_url(zone_url):
    """'https://.../zones/us-central1-a' -> 'us-central1-a'"""
    return (zone_url or 'global').rsplit('/', 1)[-1]


def _instance_network(instance):
    for iface in instance.get('networkInterfaces', []):
        if iface.get('network'):
            return _util.extract_network_name(iface['network'])
    return None


# The only metadata keys whose VALUE (not just its presence) is kept —
# see the module docstring's "Secrets exception" for why these five are
# safe: config flags, public keys, or (created-by) GCP's own resource path
# for the MIG that launched this instance — never secret-bearing free
# text.
_CHECK_RELEVANT_METADATA_KEYS = {'serial-port-enable', 'enable-oslogin', 'enable-oslogin-2fa', 'ssh-keys', 'created-by'}


def _redact_metadata(items):
    """Returns (key_names_only, secret_scan_hits, relevant_values) for an
    instance/project metadata `items` list — every value is scanned for
    secrets then discarded, except the small _CHECK_RELEVANT_METADATA_KEYS
    allowlist, whose values are kept verbatim in `relevant_values`."""
    items = items or []
    hits = []
    relevant_values = {}
    for item in items:
        value = item.get('value', '') or ''
        hits.extend(scan_text_for_secrets(value))
        key = item.get('key')
        if key in _CHECK_RELEVANT_METADATA_KEYS:
            relevant_values[key] = value
    key_names = sorted({item['key'] for item in items if item.get('key')})
    return key_names, sorted(set(hits)), relevant_values


def get_instances(compute, project_id):
    """All VM instances across all zones."""
    instances = []
    request = compute.instances().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for _zone_key, data in resp.get('items', {}).items():
            instances.extend(data.get('instances', []))
        request = compute.instances().aggregatedList_next(previous_request=request, previous_response=resp)
    return instances


def get_images(compute, project_id):
    """Non-deprecated custom images owned by this project."""
    images = []
    request = compute.images().list(project=project_id)
    while request is not None:
        resp = request.execute()
        images.extend(img for img in resp.get('items', []) if not img.get('deprecated'))
        request = compute.images().list_next(previous_request=request, previous_response=resp)
    return images


def get_image_iam_policy(compute, project_id, image_name):
    resp = compute.images().getIamPolicy(project=project_id, resource=image_name).execute()
    return resp.get('bindings', [])


def get_snapshots(compute, project_id):
    snapshots = []
    request = compute.snapshots().list(project=project_id)
    while request is not None:
        resp = request.execute()
        snapshots.extend(resp.get('items', []))
        request = compute.snapshots().list_next(previous_request=request, previous_response=resp)
    return snapshots


def get_migs(compute, project_id):
    """All managed instance groups across all zones, as (zone, mig) tuples."""
    migs = []
    request = compute.instanceGroupManagers().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for zone_key, data in resp.get('items', {}).items():
            for mig in data.get('instanceGroupManagers', []):
                migs.append((zone_key.rsplit('/', 1)[-1], mig))
        request = compute.instanceGroupManagers().aggregatedList_next(previous_request=request, previous_response=resp)
    return migs


def get_autoscalers(compute, project_id):
    """All autoscalers across all zones, keyed by their target MIG selfLink
    so gather() can merge the matching autoscaler config into that MIG's
    raw record (same "fan-out merge" idea as aws/sg.py merging rules into
    each security group)."""
    autoscalers = []
    request = compute.autoscalers().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for _zone_key, data in resp.get('items', {}).items():
            autoscalers.extend(data.get('autoscalers', []))
        request = compute.autoscalers().aggregatedList_next(previous_request=request, previous_response=resp)
    return autoscalers


def get_project_metadata_items(compute, project_id):
    resp = compute.projects().get(project=project_id).execute()
    return resp.get('commonInstanceMetadata', {}).get('items') or []


def gather(project_id, credentials, writer):
    """Gathers all Compute Engine-owned resource types for this project into
    `writer`."""
    compute = discovery.build('compute', 'v1', credentials=credentials)

    # --- VM instances ---
    try:
        instances = get_instances(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='compute_instance', message=e)
        instances = []

    for inst in instances:
        if inst.get('status') in ('TERMINATED', 'SUSPENDED'):
            continue
        zone = _zone_from_url(inst.get('zone'))
        key_names, secret_hits, relevant_values = _redact_metadata(inst.get('metadata', {}).get('items'))

        raw = dict(inst)
        if 'metadata' in raw:
            raw['metadata'] = {**raw['metadata'], 'itemKeys': key_names, 'itemValues': relevant_values}
            raw['metadata'].pop('items', None)

        writer.add_resource(
            resource_type='compute_instance',
            region=zone,
            resource_id=inst.get('selfLink', inst['name']),
            resource_name=inst['name'],
            scope_id=_instance_network(inst),
            raw=raw,
            secret_scan_hits=secret_hits,
        )

    # --- Custom images (IAM policy merged in, like aws/s3.py's per-bucket fan-out merge) ---
    try:
        images = get_images(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='compute_image', message=e)
        images = []

    for image in images:
        name = image['name']
        raw = dict(image)
        try:
            raw['_IamPolicyBindings'] = get_image_iam_policy(compute, project_id, name)
        except Exception as e:
            writer.add_error(region='global', source=f'compute_image:{name}', message=e)
        writer.add_resource(
            resource_type='compute_image',
            region='global',
            resource_id=image.get('selfLink', name),
            resource_name=name,
            raw=raw,
        )

    # --- Disk snapshots ---
    try:
        snapshots = get_snapshots(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='compute_snapshot', message=e)
        snapshots = []

    for snap in snapshots:
        writer.add_resource(
            resource_type='compute_snapshot',
            region='global',
            resource_id=snap.get('selfLink', snap['name']),
            resource_name=snap['name'],
            raw=snap,
        )

    # --- Managed instance groups (matching autoscaler config merged in) ---
    try:
        migs = get_migs(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='instance_group_manager', message=e)
        migs = []

    try:
        autoscalers_by_target = {a['target']: a for a in get_autoscalers(compute, project_id) if a.get('target')}
    except Exception as e:
        writer.add_error(region='global', source='compute_autoscaler', message=e)
        autoscalers_by_target = {}

    for zone, mig in migs:
        raw = dict(mig)
        raw['_Autoscaler'] = autoscalers_by_target.get(mig.get('selfLink', ''))
        writer.add_resource(
            resource_type='instance_group_manager',
            region=zone,
            resource_id=mig.get('selfLink', mig['name']),
            resource_name=mig['name'],
            raw=raw,
        )

    # --- Project-level metadata (ssh-keys, oslogin, ... — same redaction as instance metadata) ---
    try:
        items = get_project_metadata_items(compute, project_id)
        key_names, secret_hits, relevant_values = _redact_metadata(items)
        writer.add_resource(
            resource_type='compute_project_metadata',
            region='global',
            resource_id=project_id,
            resource_name=project_id,
            raw={'itemKeys': key_names, 'itemValues': relevant_values},
            secret_scan_hits=secret_hits,
        )
    except Exception as e:
        writer.add_error(region='global', source='compute_project_metadata', message=e)
