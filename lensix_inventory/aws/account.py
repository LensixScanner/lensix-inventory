"""Account/IAM-level gathering — IAM roles, groups, policies, server
certificates, virtual MFA devices, password policy, account summary, SSO
(Identity Center) instances/permission sets (global; IAM *users*
specifically are gathered by user.py, not here — see gather_global()'s
docstring), plus KMS keys, CloudTrail trails, AWS Config recorders/delivery
channels/aggregators, GuardDuty detectors, IAM Access Analyzer analyzers,
CloudWatch log groups, and X-Ray encryption config (regional).

Most of these resource types correspond to a pass/fail check in Lensix's
scanner, but only the underlying list/describe API call matters here — the
same "fetch first, evaluate separately" split used throughout this tool
(see `s3.py` and `sg.py`) — so each is exposed as a plain `get_*` fetcher.

Deliberately not gathered (evaluation/correlation logic, not resource
gathering):
  - Privilege-escalation-path evaluation via iam.simulate_principal_policy
    — a live "what-if" policy simulation call, not a listing of existing
    resource state.
  - Root-account access-key/usage evaluation via the IAM credential report
    (generate_credential_report + poll get_credential_report) — the
    classic stateful generate-then-poll workflow this tool's design
    explicitly excludes; not a simple list/describe call.
  - Root-usage and CIS-benchmark alarm-coverage evaluation, which searches
    CloudWatch Logs metric filters + CloudWatch alarms + EventBridge rules
    for specific keyword patterns to answer "does an alarm covering event
    X exist anywhere" — finding-time correlation, not a resource listing,
    the same category of exclusion as sg.py's cross-service "is this SG
    referenced anywhere" correlation.

Everything else — the plain list/describe calls each check fuses with its
condition check — is gathered here as raw resource data for Lensix to
evaluate server-side.
"""

import json

import boto3
import botocore


def _try(fn, *args, **kwargs):
    """Best-effort sub-API call — many of these throw when a feature isn't
    configured (e.g. NoSuchPublicAccessBlockConfiguration), which is itself
    meaningful data (absence == not configured), not an error to surface.
    Mirrors s3.py's `_try` helper."""
    try:
        return fn(*args, **kwargs)
    except botocore.exceptions.ClientError as e:
        return {'_error': e.response['Error']['Code']}
    except Exception as e:
        return {'_error': str(e)}


# --- Global (IAM/SSO) fetchers ---

def get_iam_roles():
    """list_roles already returns everything needed for unused-role and
    cross-account-external-ID evaluation (RoleLastUsed,
    AssumeRolePolicyDocument) in one call — no extra fan-out needed."""
    iam = boto3.client('iam')
    roles = []
    for page in iam.get_paginator('list_roles').paginate():
        roles.extend(page['Roles'])
    return roles


def get_iam_groups():
    """list_groups, merged per-group with member users (get_group), inline
    policy names (list_group_policies), and attached managed policies —
    covers empty-group and inline-policy evaluation."""
    iam = boto3.client('iam')
    groups = []
    for page in iam.get_paginator('list_groups').paginate():
        groups.extend(page['Groups'])

    for group in groups:
        name = group['GroupName']
        group['_Users'] = _try(iam.get_group, GroupName=name).get('Users', [])
        group['_InlinePolicyNames'] = _try(iam.list_group_policies, GroupName=name).get('PolicyNames', [])
        group['_AttachedPolicies'] = _try(iam.list_attached_group_policies, GroupName=name).get('AttachedPolicies', [])
    return groups


def get_iam_policies():
    """Customer-managed (Scope='Local') policies, merged with their default
    version's policy document — needed for PassRole-privilege evaluation."""
    iam = boto3.client('iam')
    policies = []
    for page in iam.get_paginator('list_policies').paginate(Scope='Local'):
        policies.extend(page['Policies'])

    for policy in policies:
        try:
            doc = iam.get_policy_version(
                PolicyArn=policy['Arn'],
                VersionId=policy['DefaultVersionId'],
            )['PolicyVersion']['Document']
        except Exception:
            doc = None
        policy['_PolicyDocument'] = doc
    return policies


def get_iam_server_certificates():
    iam = boto3.client('iam')
    certs = []
    for page in iam.get_paginator('list_server_certificates').paginate():
        certs.extend(page['ServerCertificateMetadataList'])
    return certs


def get_iam_virtual_mfa_devices():
    """Assigned virtual MFA devices (any principal, not just root) — used to
    evaluate whether root's MFA device is virtual (vs. hardware)."""
    iam = boto3.client('iam')
    try:
        return iam.list_virtual_mfa_devices(AssignmentStatus='Assigned').get('VirtualMFADevices', [])
    except Exception:
        return []


def get_password_policy():
    iam = boto3.client('iam')
    try:
        return {'_configured': True, **iam.get_account_password_policy()['PasswordPolicy']}
    except iam.exceptions.NoSuchEntityException:
        return {'_configured': False}
    except Exception as e:
        return {'_configured': None, '_error': str(e)}


def get_account_summary():
    iam = boto3.client('iam')
    return iam.get_account_summary()['SummaryMap']


def get_support_access_roles():
    """Roles with the AWSSupportAccess managed policy attached — needed for
    support-role-presence evaluation."""
    iam = boto3.client('iam')
    try:
        return iam.list_entities_for_policy(
            PolicyArn='arn:aws:iam::aws:policy/AWSSupportAccess',
            EntityFilter='Role',
        ).get('PolicyRoles', [])
    except Exception:
        return []


def get_sso_instances():
    try:
        sso = boto3.client('sso-admin', region_name='us-east-1')
        return sso.list_instances().get('Instances', [])
    except Exception:
        return []


def get_sso_permission_sets(instance_arn):
    sso = boto3.client('sso-admin', region_name='us-east-1')
    permission_sets = []
    for page in sso.get_paginator('list_permission_sets').paginate(InstanceArn=instance_arn):
        for ps_arn in page.get('PermissionSets', []):
            try:
                ps = sso.describe_permission_set(InstanceArn=instance_arn, PermissionSetArn=ps_arn)['PermissionSet']
                permission_sets.append(ps)
            except Exception:
                continue
    return permission_sets


# --- Regional fetchers ---

def get_kms_keys(region):
    """list_keys, merged per (customer-managed) key with describe_key,
    key rotation status, key policy, and alias — covers key-rotation and
    public/unused-key findings. AWS-managed keys are skipped via
    `if meta.get('KeyManager') == 'AWS': continue`."""
    kms = boto3.client('kms', region_name=region)
    keys = []
    for page in kms.get_paginator('list_keys').paginate():
        keys.extend(page['Keys'])

    out = []
    for key in keys:
        key_id = key['KeyId']
        try:
            meta = kms.describe_key(KeyId=key_id)['KeyMetadata']
        except Exception:
            continue
        if meta.get('KeyManager') == 'AWS':
            continue
        raw = dict(meta)
        raw['_RotationStatus'] = _try(kms.get_key_rotation_status, KeyId=key_id)
        raw['_Aliases'] = _try(kms.list_aliases, KeyId=key_id).get('Aliases', []) if meta.get('KeyState') != 'PendingDeletion' else []
        policy_raw = _try(kms.get_key_policy, KeyId=key_id, PolicyName='default')
        policy_doc = None
        if isinstance(policy_raw, dict) and 'Policy' in policy_raw:
            try:
                policy_doc = json.loads(policy_raw['Policy'])
            except Exception:
                policy_doc = None
        raw['_KeyPolicy'] = policy_doc
        out.append(raw)
    return out


def get_cloudtrail_trails(region):
    """describe_trails, merged per trail with event selectors, trail
    status, and (for the trail's own S3 bucket) bucket logging/policy/
    public-access-block config — the same fused fan-out pattern as
    s3.py's get_bucket_metadata, covering trail-enabled, data-event, and
    trail-bucket-security evaluation."""
    ct = boto3.client('cloudtrail', region_name=region)
    s3 = boto3.client('s3')
    try:
        trails = ct.describe_trails(includeShadowTrails=False).get('trailList', [])
    except Exception:
        return []

    for trail in trails:
        arn = trail.get('TrailARN', trail.get('Name', ''))
        trail['_EventSelectors'] = _try(ct.get_event_selectors, TrailName=arn)
        trail['_TrailStatus'] = _try(ct.get_trail_status, Name=arn)

        bucket = trail.get('S3BucketName')
        if bucket:
            trail['_BucketLogging'] = _try(s3.get_bucket_logging, Bucket=bucket)
            policy_raw = _try(s3.get_bucket_policy, Bucket=bucket)
            policy_doc = None
            if isinstance(policy_raw, dict) and 'Policy' in policy_raw:
                try:
                    policy_doc = json.loads(policy_raw['Policy'])
                except Exception:
                    policy_doc = None
            trail['_BucketPolicy'] = policy_doc
            trail['_BucketPublicAccessBlock'] = _try(s3.get_public_access_block, Bucket=bucket)
    return trails


def get_config_recorders(region):
    """describe_configuration_recorders merged with recorder status —
    covers config-enabled and config-coverage evaluation."""
    cfg = boto3.client('config', region_name=region)
    try:
        recorders = cfg.describe_configuration_recorders().get('ConfigurationRecorders', [])
    except Exception:
        return []
    statuses = {}
    try:
        for s in cfg.describe_configuration_recorder_status().get('ConfigurationRecordersStatus', []):
            statuses[s['name']] = s
    except Exception:
        pass
    for recorder in recorders:
        recorder['_Status'] = statuses.get(recorder.get('name'))
    return recorders


def get_config_delivery_channels(region):
    cfg = boto3.client('config', region_name=region)
    try:
        return cfg.describe_delivery_channel_status().get('DeliveryChannelsStatus', [])
    except Exception:
        return []


def get_config_aggregators(region):
    cfg = boto3.client('config', region_name=region)
    try:
        return cfg.describe_configuration_aggregators().get('ConfigurationAggregators', [])
    except Exception:
        return []


def get_guardduty_detectors(region):
    """list_detectors merged with per-detector detail (get_detector) —
    covers GuardDuty-enabled evaluation."""
    gd = boto3.client('guardduty', region_name=region)
    try:
        detector_ids = gd.list_detectors().get('DetectorIds', [])
    except Exception:
        return []
    detectors = []
    for detector_id in detector_ids:
        try:
            det = gd.get_detector(DetectorId=detector_id)
            det['DetectorId'] = detector_id
            detectors.append(det)
        except Exception:
            continue
    return detectors


def get_access_analyzers(region):
    try:
        aa = boto3.client('accessanalyzer', region_name=region)
        return aa.list_analyzers().get('analyzers', [])
    except Exception:
        return []


def get_log_groups(region):
    """describe_log_groups — a plain listing (retentionInDays is static
    config, not a time-windowed metric query), covers log-retention
    evaluation."""
    logs = boto3.client('logs', region_name=region)
    groups = []
    try:
        for page in logs.get_paginator('describe_log_groups').paginate():
            groups.extend(page['logGroups'])
    except Exception:
        pass
    return groups


def get_xray_encryption_config(region):
    try:
        xray = boto3.client('xray', region_name=region)
        return xray.get_encryption_config().get('EncryptionConfig', {})
    except Exception:
        return None


# --- Gatherers ---

def gather_global(writer, account_id):
    """Gathers account-wide IAM/SSO resources once (not per-region).
    Note: `iam_user` is deliberately NOT gathered here — user.py owns that
    resource type (it's the 1:1 port of user_checks.py) to avoid gathering
    the same users twice; see user.py's docstring."""
    for role in get_iam_roles():
        writer.add_resource(
            resource_type='iam_role', region='global', resource_id=role['RoleId'],
            resource_name=role['RoleName'], raw=role,
        )

    for group in get_iam_groups():
        writer.add_resource(
            resource_type='iam_group', region='global', resource_id=group['GroupId'],
            resource_name=group['GroupName'], raw=group,
        )

    for policy in get_iam_policies():
        writer.add_resource(
            resource_type='iam_policy', region='global', resource_id=policy['Arn'],
            resource_name=policy['PolicyName'], raw=policy,
        )

    for cert in get_iam_server_certificates():
        writer.add_resource(
            resource_type='iam_server_certificate', region='global',
            resource_id=cert.get('Arn', cert.get('ServerCertificateName')),
            resource_name=cert.get('ServerCertificateName', cert.get('Arn')), raw=cert,
        )

    for device in get_iam_virtual_mfa_devices():
        serial = device['SerialNumber']
        writer.add_resource(
            resource_type='iam_virtual_mfa_device', region='global', resource_id=serial,
            resource_name=serial, raw=device,
        )

    pw_policy = get_password_policy()
    writer.add_resource(
        resource_type='iam_password_policy', region='global', resource_id='password_policy',
        resource_name='password_policy', raw=pw_policy,
    )

    summary_raw = {
        'SummaryMap': get_account_summary(),
        '_SupportAccessRoles': get_support_access_roles(),
    }
    writer.add_resource(
        resource_type='iam_account_summary', region='global', resource_id='account_summary',
        resource_name='account_summary', raw=summary_raw,
    )

    for instance in get_sso_instances():
        instance_arn = instance.get('InstanceArn')
        writer.add_resource(
            resource_type='sso_instance', region='global', resource_id=instance_arn,
            resource_name=instance.get('IdentityStoreId', instance_arn), raw=instance,
        )
        if instance_arn:
            for ps in get_sso_permission_sets(instance_arn):
                ps_arn = ps.get('PermissionSetArn')
                writer.add_resource(
                    resource_type='sso_permission_set', region='global', resource_id=ps_arn,
                    resource_name=ps.get('Name', ps_arn), scope_id=instance_arn, raw=ps,
                )


def gather(region, writer):
    """Gathers per-region account/security-baseline resources."""
    for key in get_kms_keys(region):
        key_id = key['KeyId']
        name = key_id
        for alias in key.get('_Aliases', []):
            name = alias.get('AliasName', key_id)
            break
        writer.add_resource(
            resource_type='kms_key', region=region, resource_id=key_id,
            resource_name=name, raw=key,
        )

    for trail in get_cloudtrail_trails(region):
        arn = trail.get('TrailARN', trail.get('Name'))
        writer.add_resource(
            resource_type='cloudtrail_trail', region=region, resource_id=arn,
            resource_name=trail.get('Name', arn), raw=trail,
        )

    for recorder in get_config_recorders(region):
        name = recorder.get('name', region)
        writer.add_resource(
            resource_type='config_recorder', region=region, resource_id=name,
            resource_name=name, raw=recorder,
        )

    for channel in get_config_delivery_channels(region):
        name = channel.get('name', region)
        writer.add_resource(
            resource_type='config_delivery_channel', region=region, resource_id=name,
            resource_name=name, raw=channel,
        )

    for agg in get_config_aggregators(region):
        name = agg.get('ConfigurationAggregatorName', region)
        writer.add_resource(
            resource_type='config_aggregator', region=region, resource_id=name,
            resource_name=name, raw=agg,
        )

    for detector in get_guardduty_detectors(region):
        detector_id = detector['DetectorId']
        writer.add_resource(
            resource_type='guardduty_detector', region=region, resource_id=detector_id,
            resource_name=detector_id, raw=detector,
        )

    for analyzer in get_access_analyzers(region):
        arn = analyzer.get('arn')
        writer.add_resource(
            resource_type='access_analyzer', region=region, resource_id=arn,
            resource_name=analyzer.get('name', arn), raw=analyzer,
        )

    for lg in get_log_groups(region):
        name = lg.get('logGroupName')
        writer.add_resource(
            resource_type='cloudwatch_log_group', region=region, resource_id=name,
            resource_name=name, raw=lg,
        )

    xray_cfg = get_xray_encryption_config(region)
    if xray_cfg is not None:
        writer.add_resource(
            resource_type='xray_encryption_config', region=region, resource_id=f'xray-{region}',
            resource_name=f'xray-{region}', raw=xray_cfg,
        )
