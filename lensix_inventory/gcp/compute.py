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


def _mig_instance_template_name(mig):
    """Same fallback compute_import.py's own _mig_template() uses —
    the MIG's top-level instanceTemplate reference first, falling back to
    the first entry in `versions` for a MIG using multiple template
    versions (a canary rollout). Only the primary reference is checked
    here, the same simplification AWS's ASG launch-template lookup makes
    for MixedInstancesPolicy — see autoscaling.py's own
    _launches_with_public_ip docstring. Returns just the template's own
    name (the API wants that, not the full selfLink URL the MIG's raw
    record carries)."""
    template_url = mig.get('instanceTemplate') or (mig.get('versions') or [{}])[0].get('instanceTemplate', '')
    return template_url.rsplit('/', 1)[-1] if template_url else None


def _network_interfaces_have_access_config(network_interfaces):
    """True if any network interface in an instance template's own
    `properties` declares an accessConfigs entry at all. Unlike a LIVE
    instance — where accessConfigs[].natIP holds the actual assigned IP
    address, see compute_import.py's own _has_public_ip — a TEMPLATE's
    accessConfigs entries never carry a concrete IP at all (that's only
    assigned once a real instance is created from the template); the
    entry's mere PRESENCE is what causes GCP to allocate an ephemeral
    external IP for every instance the template produces. False if
    network interfaces are declared but none of them have any
    accessConfigs entries; None if there's no network interface data to
    judge from at all (a deleted/inaccessible template)."""
    if network_interfaces is None:
        return None
    for iface in network_interfaces:
        if iface.get('accessConfigs'):
            return True
    return False


def _mig_launches_with_public_ip(compute, project_id, mig):
    """True/False/None for whether this MIG's instance template assigns
    instances an external IP — None when the MIG has no resolvable
    template reference, or the template lookup returns no network
    interface data to judge from. Any lookup FAILURE (permission denied,
    throttling, a deleted template returning an HttpError, ...) raises
    instead of returning None — gather()'s own caller is the error
    boundary (see its own comment), matching the discipline
    autoscaling.py's _launches_with_public_ip follows after an earlier
    version of that function silently swallowed lookup failures with
    zero trace anywhere."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    network_interfaces = (template.get('properties') or {}).get('networkInterfaces')
    return _network_interfaces_have_access_config(network_interfaces)


def _mig_instance_template_service_accounts(compute, project_id, mig):
    """The raw `serviceAccounts` list from this MIG's instance template
    (each entry an {'email': ..., 'scopes': [...]} dict, the SDK's native
    shape) — None when the MIG has no resolvable template reference, or
    the template has no serviceAccounts entry at all. A separate template
    lookup from _mig_launches_with_public_ip's own (rather than sharing
    one fetch) deliberately mirrors that function's exact contract —
    same fetch-by-name, same "raise on lookup failure, caller is the
    error boundary" discipline — so this one MIG-level lookup doesn't
    need its own bespoke error handling; gather()'s existing try/except
    around each derived MIG field already covers it. Used to evaluate
    lensix-scanner-light's compute_defaultserviceaccount/
    compute_fullapiaccess checks once per MIG instead of once per member
    instance (see compute_migpublicip for the established precedent this
    follows)."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('serviceAccounts')


def _mig_instance_template_can_ip_forward(compute, project_id, mig):
    """template.properties.canIpForward (bool) or None if the MIG has no
    resolvable template reference, or the template has no canIpForward
    entry at all — a third, independent template fetch from
    _mig_launches_with_public_ip's/_mig_instance_template_service_accounts's
    own (same deliberate "isolate failure per fact" trade-off already
    accepted this session for the service-account addition, at the cost
    of one more API call per MIG rather than refactoring the existing
    lookups to share one fetch). Same "raise on lookup failure, caller is
    the error boundary" discipline as its siblings — gather()'s existing
    try/except around each derived MIG field already covers it. Used to
    evaluate lensix-scanner-light's compute_migipforwarding check once per
    MIG instead of once per member instance."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('canIpForward')


def _mig_instance_template_disks(compute, project_id, mig):
    """The raw properties.disks[] list from this MIG's instance template —
    same AttachedDisk shape check_nodiskencryption already reads off a
    live instance's own disks[], so compute_migdiskencryption_raw can
    reuse its exact boot-disk-lookup logic against this instead of
    writing new field paths. None when the MIG has no resolvable template
    reference, or the template has no disks entry at all. A fourth,
    independent template fetch from its siblings' own — same discipline
    as _mig_instance_template_can_ip_forward above."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('disks')


def _mig_instance_template_shielded_config(compute, project_id, mig):
    """The raw properties.shieldedInstanceConfig dict from this MIG's
    instance template (same {'enableSecureBoot': ..., 'enableVtpm': ...,
    'enableIntegrityMonitoring': ...} shape check_noshieldedboot/
    check_novtpm/check_nointegritymonitoring already read off a live
    instance's own shieldedInstanceConfig), so compute_mignoshieldedboot_raw/
    compute_mignovtpm_raw/compute_mignointegritymonitoring_raw can reuse
    those three checks' exact condition logic against this one shared
    field instead of writing new field paths — three check_ids, one
    fetch. None when the MIG has no resolvable template reference, or the
    template has no shieldedInstanceConfig entry at all. A fifth,
    independent template fetch from its siblings' own — same discipline
    as _mig_instance_template_disks above."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('shieldedInstanceConfig')


def _mig_instance_template_scheduling(compute, project_id, mig):
    """The raw properties.scheduling dict from this MIG's instance
    template (same {'automaticRestart': ..., 'onHostMaintenance': ...}
    shape check_noautorestart/check_nomaintmigration already read off a
    live instance's own scheduling), so compute_mignoautorestart_raw/
    compute_mignomaintmigration_raw can reuse those two checks' exact
    condition logic against this one shared field — two check_ids, one
    fetch. None when the MIG has no resolvable template reference, or the
    template has no scheduling entry at all. A sixth, independent
    template fetch from its siblings' own — same discipline as
    _mig_instance_template_disks above."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('scheduling')


def _mig_instance_template_metadata_items(compute, project_id, mig):
    """The raw properties.metadata.items list from this MIG's instance
    template — the exact same raw shape the per-instance path reads off
    instance.metadata.items, so this module's own already-generic
    `_redact_metadata(items)` helper (see its own docstring) can be
    reused as-is against it, no new secret-scanning logic needed.
    Backs compute_migserialport_raw/compute_migoslogin_raw/
    compute_migoslogin2fa_raw/compute_migsecretsinmetadata_raw — four
    check_ids sharing this one fetch. None when the MIG has no resolvable
    template reference, or the template has no metadata entry at all —
    `_redact_metadata(None)` already degrades that case to empty results,
    same as it does for an instance with no metadata at all, so callers
    don't need a separate None-guard around it. A seventh, independent
    template fetch from its siblings' own — same discipline as
    _mig_instance_template_disks above."""
    template_name = _mig_instance_template_name(mig)
    if not template_name:
        return None
    template = compute.instanceTemplates().get(project=project_id, instanceTemplate=template_name).execute()
    return (template.get('properties') or {}).get('metadata', {}).get('items')


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
            tags=raw.get('labels'),
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
            tags=raw.get('labels'),
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
            tags=snap.get('labels'),
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
        try:
            raw['_InstanceTemplatePublicIp'] = _mig_launches_with_public_ip(compute, project_id, mig)
        except Exception as e:
            # One MIG's template lookup failing doesn't abort the rest of
            # the region — recorded via add_error (-> scan_errors) rather
            # than swallowed, same reasoning as autoscaling.py's own
            # equivalent gather() loop.
            raw['_InstanceTemplatePublicIp'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            raw['_InstanceTemplateServiceAccounts'] = _mig_instance_template_service_accounts(compute, project_id, mig)
        except Exception as e:
            # Same isolation as the public-IP lookup above — a second,
            # independent template fetch (not shared with it) so either
            # one failing doesn't take the other down.
            raw['_InstanceTemplateServiceAccounts'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            raw['_InstanceTemplateCanIpForward'] = _mig_instance_template_can_ip_forward(compute, project_id, mig)
        except Exception as e:
            # Same isolation as the two lookups above — a third,
            # independent template fetch so a failure here doesn't take
            # the others down.
            raw['_InstanceTemplateCanIpForward'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            raw['_InstanceTemplateDisks'] = _mig_instance_template_disks(compute, project_id, mig)
        except Exception as e:
            # Same isolation as the three lookups above — a fourth,
            # independent template fetch so a failure here doesn't take
            # the others down.
            raw['_InstanceTemplateDisks'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            raw['_InstanceTemplateShieldedConfig'] = _mig_instance_template_shielded_config(compute, project_id, mig)
        except Exception as e:
            # Same isolation as the four lookups above — a fifth,
            # independent template fetch so a failure here doesn't take
            # the others down.
            raw['_InstanceTemplateShieldedConfig'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            raw['_InstanceTemplateScheduling'] = _mig_instance_template_scheduling(compute, project_id, mig)
        except Exception as e:
            # Same isolation as the five lookups above — a sixth,
            # independent template fetch so a failure here doesn't take
            # the others down.
            raw['_InstanceTemplateScheduling'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        try:
            items = _mig_instance_template_metadata_items(compute, project_id, mig)
            key_names, secret_hits, relevant_values = _redact_metadata(items)
            raw['_InstanceTemplateMetadataItemValues'] = relevant_values
            raw['_InstanceTemplateMetadataSecretHits'] = secret_hits
        except Exception as e:
            # Same isolation as the six lookups above — a seventh,
            # independent template fetch so a failure here doesn't take
            # the others down. This brings the MIG gather step to 7
            # independent instanceTemplates().get() calls per MIG — flagged
            # explicitly (see the plan this shipped from): a future
            # consolidation refactor threading one shared fetch into all 7
            # extractor functions would cut this to 1 call per MIG, but
            # changing the 4 already-shipped, already-tested functions'
            # signatures is a larger, separate, backward-compatibility-
            # sensitive change — deliberately deferred, not bundled here.
            raw['_InstanceTemplateMetadataItemValues'] = None
            raw['_InstanceTemplateMetadataSecretHits'] = None
            writer.add_error(region=zone, source='instance_group_manager (instance template lookup)',
                              message=f"{mig.get('name', '')}: {e}")
        # No tags= here: InstanceGroupManager has no `labels` field in the
        # Compute Engine v1 API at all (confirmed against the real
        # discovery document schema, same check that caught vpc.py's own
        # mistake — see docs/tag-suppressions.md) — a genuine
        # architectural N/A, same class as kms.py's own KeyRing.
        writer.add_resource(
            resource_type='instance_group_manager',
            region=zone,
            resource_id=mig.get('selfLink', mig['name']),
            resource_name=mig['name'],
            raw=raw,
        )

    # --- Project-level metadata (ssh-keys, oslogin, ... — same redaction as instance metadata) ---
    # No tags= on compute_project_metadata: it's a synthetic, project-wide
    # singleton (the project's own commonInstanceMetadata, not a real
    # listable resource with its own id/labels) — same architectural N/A
    # class as AWS's account.py synthetics (iam_password_policy,
    # iam_account_summary, ...).
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
