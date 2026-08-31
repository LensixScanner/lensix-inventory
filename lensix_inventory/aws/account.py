"""Account/IAM-level gathering — IAM roles, groups, policies, server
certificates, virtual MFA devices, password policy, account summary, SSO
(Identity Center) instances/permission sets, and the IAM credential
report's root-account row as `iam_root` (global; IAM *users* specifically
are gathered by user.py, not here — see gather_global()'s docstring),
plus KMS keys, CloudTrail trails, AWS Config recorders/delivery
channels/aggregators, GuardDuty detectors, IAM Access Analyzer analyzers,
CloudWatch log groups, and X-Ray encryption config (regional).

Most of these resource types correspond to a pass/fail check in Lensix's
scanner, but only the underlying list/describe API call matters here — the
same "fetch first, evaluate separately" split used throughout this tool
(see `s3.py` and `sg.py`) — so each is exposed as a plain `get_*` fetcher.

Deliberately not gathered (evaluation logic, not resource gathering):
  - Privilege-escalation-path evaluation via iam.simulate_principal_policy
    — a live "what-if" policy simulation call, not a listing of existing
    resource state.

Root-usage and CIS-benchmark alarm-coverage evaluation ARE gathered now
(get_metric_filters_with_alarms, get_eventbridge_rules) as
`cloudwatch_metric_filter`/`eventbridge_rule` resources — the actual
keyword-pattern correlation ("does an alarm covering event X exist
anywhere") stays a check-time concern (same "gather raw, correlate
separately" split sg.py's own get_attached_sg_ids() uses for its
cross-service fan-out), it just no longer needs a live call to do it.
gather()'s log groups come from the SAME region's already-fetched trails
(CloudWatchLogsLogGroupArn — no extra describe_trails call); gather_global()
takes an optional `regions` list and re-fetches trails per region (a
deliberate, accepted duplicate of gather()'s own per-region trail fetch —
same tradeoff lb.py's dual target_group gather already established) since
root-usage coverage needs to be evaluated once across every region, not
per-region, and gather_global() has no other way to know what regions
exist.

Everything else — the plain list/describe calls each check fuses with its
condition check, plus the credential report's root row (a stateful
generate-then-poll workflow, but still just a listing of existing
account state) — is gathered here as raw resource data for Lensix to
evaluate server-side.

Tag/label support (for tag-based suppression) is wired per resource type
based on what AWS actually supports tagging:
  - Inline already: iam_role, iam_policy, iam_virtual_mfa_device,
    guardduty_detector, access_analyzer.
  - A separate tag-fetch call: sso_instance/sso_permission_set (shared
    get_sso_tags helper), kms_key, cloudtrail_trail, eventbridge_rule,
    config_aggregator, cloudwatch_log_group.
  - Genuinely not taggable in AWS — no tags= passed at all:
    iam_group, iam_server_certificate (neither has a tagging API),
    config_recorder/config_delivery_channel/cloudwatch_metric_filter
    (none of these three are independently taggable AWS resources), and
    the synthetic single-record types with no real underlying resource to
    tag (iam_password_policy, iam_account_summary, iam_root,
    xray_encryption_config).
"""

import csv
import io
import json
import time

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


def get_sso_tags(instance_arn, resource_arn):
    """Generic across both sso_instance and sso_permission_set — Identity
    Center's own ListTagsForResource always takes the instance ARN plus
    whichever resource's ARN you're asking about (the instance's own ARN
    for itself). Returns [] on failure."""
    sso = boto3.client('sso-admin', region_name='us-east-1')
    tags = []
    try:
        kwargs = {'InstanceArn': instance_arn, 'ResourceArn': resource_arn}
        while True:
            resp = sso.list_tags_for_resource(**kwargs)
            tags.extend(resp.get('Tags', []))
            next_token = resp.get('NextToken')
            if not next_token:
                break
            kwargs['NextToken'] = next_token
    except Exception:
        return []
    return tags


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


def get_kms_key_tags(region, key_id):
    """describe_key doesn't include tags — KMS's own paginated
    list_resource_tags call. Returns [] on failure."""
    kms = boto3.client('kms', region_name=region)
    tags = []
    try:
        kwargs = {'KeyId': key_id}
        while True:
            resp = kms.list_resource_tags(**kwargs)
            tags.extend(resp.get('Tags', []))
            if not resp.get('Truncated'):
                break
            kwargs['Marker'] = resp.get('NextMarker')
    except Exception:
        return []
    return tags


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


def get_trail_tags(region, trail_arn):
    """describe_trails doesn't include tags — CloudTrail's own ListTags
    call (batched by design, but called one ARN at a time here to keep
    the per-trail failure isolated). Returns [] on failure."""
    ct = boto3.client('cloudtrail', region_name=region)
    try:
        resp = ct.list_tags(ResourceIdList=[trail_arn])
        for entry in resp.get('ResourceTagList', []):
            if entry.get('ResourceId') == trail_arn:
                return entry.get('TagsList', [])
        return []
    except Exception:
        return []


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


def get_config_aggregator_tags(region, arn):
    """describe_configuration_aggregators doesn't include tags — Config's
    own paginated list_tags_for_resource call. Returns [] on failure."""
    cfg = boto3.client('config', region_name=region)
    tags = []
    try:
        kwargs = {'ResourceArn': arn}
        while True:
            resp = cfg.list_tags_for_resource(**kwargs)
            tags.extend(resp.get('Tags', []))
            next_token = resp.get('NextToken')
            if not next_token:
                break
            kwargs['NextToken'] = next_token
    except Exception:
        return []
    return tags


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


def get_log_group_tags(region, log_group_arn):
    """describe_log_groups doesn't include tags — Logs' own
    list_tags_for_resource call (unpaginated; log groups have supported
    the newer ARN-keyed tagging API since 2021 — list_tags_log_group is
    the older, name-keyed equivalent, not needed here). Returns {} on
    failure."""
    logs = boto3.client('logs', region_name=region)
    try:
        return logs.list_tags_for_resource(resourceArn=log_group_arn).get('tags', {})
    except Exception:
        return {}


def get_xray_encryption_config(region):
    try:
        xray = boto3.client('xray', region_name=region)
        return xray.get_encryption_config().get('EncryptionConfig', {})
    except Exception:
        return None


def get_trail_log_group_names(trails):
    """Derives CloudWatch Logs log group names from already-fetched trail
    records (CloudWatchLogsLogGroupArn) — pure, no API call of its own."""
    names = []
    for t in trails:
        arn = t.get('CloudWatchLogsLogGroupArn')
        if arn:
            names.append(arn.split(':log-group:')[1].split(':')[0])
    return names


def get_metric_filters_with_alarms(region, log_group):
    """describe_metric_filters for one log group, each filter merged with
    the CloudWatch alarms watching its metric(s) (`_Alarms`) — covers the
    CIS-benchmark/root-usage "is there an alarm for event X" checks, which
    need both the filter's pattern text and whether a real alarm (with
    actions) is wired up to it."""
    logs = boto3.client('logs', region_name=region)
    cw = boto3.client('cloudwatch', region_name=region)
    filters = []
    for page in logs.get_paginator('describe_metric_filters').paginate(logGroupName=log_group):
        filters.extend(page['metricFilters'])
    for f in filters:
        alarms = []
        for mt in f.get('metricTransformations', []):
            alarms.extend(_try(
                cw.describe_alarms_for_metric,
                MetricName=mt['metricName'], Namespace=mt['metricNamespace'],
            ).get('MetricAlarms', []))
        f['_Alarms'] = alarms
    return filters


def get_eventbridge_rules(region):
    """list_rules merged with each rule's targets (`_Targets`) — the
    alternative alarm-coverage mechanism to a CloudWatch Logs metric
    filter + alarm."""
    events = boto3.client('events', region_name=region)
    rules = []
    for page in events.get_paginator('list_rules').paginate():
        rules.extend(page.get('Rules', []))
    for rule in rules:
        rule['_Targets'] = _try(
            events.list_targets_by_rule,
            Rule=rule['Name'], EventBusName=rule.get('EventBusName', 'default'),
        ).get('Targets', [])
    return rules


def get_eventbridge_rule_tags(region, rule_arn):
    """list_rules doesn't include tags — EventBridge's own (unpaginated)
    list_tags_for_resource call. Returns [] on failure."""
    events = boto3.client('events', region_name=region)
    try:
        return events.list_tags_for_resource(ResourceARN=rule_arn).get('Tags', [])
    except Exception:
        return []


def get_root_credential_report_row():
    """Same generate-then-poll workflow as user.py's own
    get_credential_report() (duplicated rather than shared — this module
    and user.py are gathered by two separate live containers, so there's
    no way to share the actual live call between them either way), but
    only the `<root_account>` row is kept — user.py's own per-user merge
    explicitly drops that row since root is never a list_users() result."""
    iam = boto3.client('iam')
    try:
        iam.generate_credential_report()
    except iam.exceptions.LimitExceededException:
        pass
    content = None
    for _ in range(15):
        try:
            resp = iam.get_credential_report()
            content = resp['Content'].decode('utf-8')
            break
        except iam.exceptions.CredentialReportNotReadyException:
            time.sleep(2)
    if content is None:
        raise TimeoutError('Credential report not ready after 15 retries')
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        if row.get('user') == '<root_account>':
            return row
    return None


# --- Gatherers ---

def gather_global(writer, account_id, regions=None):
    """Gathers account-wide IAM/SSO resources once (not per-region).
    Note: `iam_user` is deliberately NOT gathered here — user.py owns that
    resource type, to avoid gathering the same users twice; see user.py's
    docstring.

    Ten independent fetches — isolate each so one's failure doesn't
    discard the others. When `regions` is given, also re-fetches trails +
    metric-filter/alarm + EventBridge data per region (a deliberate,
    accepted duplicate of gather()'s own per-region fetch — see this
    module's own docstring) so root-usage alarm coverage, which has to be
    evaluated once across every region rather than per-region, has
    something to read regardless of which regions scan_region() itself
    ends up covering."""
    try:
        roles = get_iam_roles()
    except Exception as e:
        writer.add_error(region='global', source='account (iam roles)', message=e)
        roles = []
    for role in roles:
        writer.add_resource(
            resource_type='iam_role', region='global', resource_id=role['RoleId'],
            resource_name=role['RoleName'], raw=role, tags=role.get('Tags'),
        )

    try:
        groups = get_iam_groups()
    except Exception as e:
        writer.add_error(region='global', source='account (iam groups)', message=e)
        groups = []
    for group in groups:
        writer.add_resource(
            resource_type='iam_group', region='global', resource_id=group['GroupId'],
            resource_name=group['GroupName'], raw=group,
        )

    try:
        policies = get_iam_policies()
    except Exception as e:
        writer.add_error(region='global', source='account (iam policies)', message=e)
        policies = []
    for policy in policies:
        writer.add_resource(
            resource_type='iam_policy', region='global', resource_id=policy['Arn'],
            resource_name=policy['PolicyName'], raw=policy, tags=policy.get('Tags'),
        )

    try:
        certs = get_iam_server_certificates()
    except Exception as e:
        writer.add_error(region='global', source='account (server certificates)', message=e)
        certs = []
    for cert in certs:
        writer.add_resource(
            resource_type='iam_server_certificate', region='global',
            resource_id=cert.get('Arn', cert.get('ServerCertificateName')),
            resource_name=cert.get('ServerCertificateName', cert.get('Arn')), raw=cert,
        )

    try:
        devices = get_iam_virtual_mfa_devices()
    except Exception as e:
        writer.add_error(region='global', source='account (virtual mfa devices)', message=e)
        devices = []
    for device in devices:
        serial = device['SerialNumber']
        writer.add_resource(
            resource_type='iam_virtual_mfa_device', region='global', resource_id=serial,
            resource_name=serial, raw=device, tags=device.get('Tags'),
        )

    try:
        pw_policy = get_password_policy()
    except Exception as e:
        writer.add_error(region='global', source='account (password policy)', message=e)
        pw_policy = {'_configured': None}
    writer.add_resource(
        resource_type='iam_password_policy', region='global', resource_id='password_policy',
        resource_name='password_policy', raw=pw_policy,
    )

    try:
        summary_raw = {
            'SummaryMap': get_account_summary(),
            '_SupportAccessRoles': get_support_access_roles(),
        }
    except Exception as e:
        writer.add_error(region='global', source='account (account summary)', message=e)
        summary_raw = None
    if summary_raw is not None:
        writer.add_resource(
            resource_type='iam_account_summary', region='global', resource_id='account_summary',
            resource_name='account_summary', raw=summary_raw,
        )

    try:
        sso_instances = get_sso_instances()
    except Exception as e:
        writer.add_error(region='global', source='account (sso instances)', message=e)
        sso_instances = []
    for instance in sso_instances:
        instance_arn = instance.get('InstanceArn')
        writer.add_resource(
            resource_type='sso_instance', region='global', resource_id=instance_arn,
            resource_name=instance.get('IdentityStoreId', instance_arn), raw=instance,
            tags=get_sso_tags(instance_arn, instance_arn) if instance_arn else None,
        )
        if instance_arn:
            try:
                permission_sets = get_sso_permission_sets(instance_arn)
            except Exception as e:
                writer.add_error(region='global', source=f'account (sso permission sets:{instance_arn})', message=e)
                permission_sets = []
            for ps in permission_sets:
                ps_arn = ps.get('PermissionSetArn')
                writer.add_resource(
                    resource_type='sso_permission_set', region='global', resource_id=ps_arn,
                    resource_name=ps.get('Name', ps_arn), scope_id=instance_arn, raw=ps,
                    tags=get_sso_tags(instance_arn, ps_arn) if ps_arn else None,
                )

    try:
        root_row = get_root_credential_report_row()
    except Exception as e:
        writer.add_error(region='global', source='account (root credential report)', message=e)
        root_row = None
    writer.add_resource(
        resource_type='iam_root', region='global', resource_id='root',
        resource_name='root', raw=root_row or {},
    )

    for region in (regions or []):
        try:
            trails = get_cloudtrail_trails(region)
        except Exception as e:
            writer.add_error(region=region, source='account (cloudtrail trails, root-usage coverage)', message=e)
            trails = []
        try:
            for log_group in get_trail_log_group_names(trails):
                for f in get_metric_filters_with_alarms(region, log_group):
                    filter_name = f.get('filterName', log_group)
                    writer.add_resource(
                        resource_type='cloudwatch_metric_filter', region=region,
                        resource_id=f'{log_group}:{filter_name}', resource_name=filter_name, raw=f,
                    )
        except Exception as e:
            writer.add_error(region=region, source='account (metric filters, root-usage coverage)', message=e)
        try:
            for rule in get_eventbridge_rules(region):
                name = rule.get('Name', region)
                rule_arn = rule.get('Arn')
                writer.add_resource(
                    resource_type='eventbridge_rule', region=region, resource_id=name,
                    resource_name=name, raw=rule,
                    tags=get_eventbridge_rule_tags(region, rule_arn) if rule_arn else None,
                )
        except Exception as e:
            writer.add_error(region=region, source='account (eventbridge rules, root-usage coverage)', message=e)


def gather(region, writer):
    """Gathers per-region account/security-baseline resources.

    Eleven independent fetches — isolate each so one's failure doesn't
    discard the others. (Metric filters/EventBridge rules derive their
    log groups from the trails fetch above rather than re-listing trails,
    so they're isolated from each other but share that one upstream
    dependency — a trails failure means no log groups to look up, not a
    metric-filters failure of its own.)"""
    try:
        keys = get_kms_keys(region)
    except Exception as e:
        writer.add_error(region=region, source='account (kms keys)', message=e)
        keys = []
    for key in keys:
        key_id = key['KeyId']
        name = key_id
        for alias in key.get('_Aliases', []):
            name = alias.get('AliasName', key_id)
            break
        writer.add_resource(
            resource_type='kms_key', region=region, resource_id=key_id,
            resource_name=name, raw=key, tags=get_kms_key_tags(region, key_id),
        )

    try:
        trails = get_cloudtrail_trails(region)
    except Exception as e:
        writer.add_error(region=region, source='account (cloudtrail trails)', message=e)
        trails = []
    for trail in trails:
        arn = trail.get('TrailARN', trail.get('Name'))
        writer.add_resource(
            resource_type='cloudtrail_trail', region=region, resource_id=arn,
            resource_name=trail.get('Name', arn), raw=trail,
            tags=get_trail_tags(region, arn) if arn else None,
        )

    # Alarm coverage — metric filters (with their alarms resolved) for
    # every log group the region's own trails deliver to, plus EventBridge
    # rules. Not the correlation itself (see this module's own docstring)
    # — just the raw filter/rule data the CIS-benchmark and root-usage
    # checks need, gathered once here instead of on every check evaluation.
    try:
        for log_group in get_trail_log_group_names(trails):
            for f in get_metric_filters_with_alarms(region, log_group):
                filter_name = f.get('filterName', log_group)
                writer.add_resource(
                    resource_type='cloudwatch_metric_filter', region=region,
                    resource_id=f'{log_group}:{filter_name}', resource_name=filter_name, raw=f,
                )
    except Exception as e:
        writer.add_error(region=region, source='account (metric filters)', message=e)

    try:
        for rule in get_eventbridge_rules(region):
            name = rule.get('Name', region)
            rule_arn = rule.get('Arn')
            writer.add_resource(
                resource_type='eventbridge_rule', region=region, resource_id=name,
                resource_name=name, raw=rule,
                tags=get_eventbridge_rule_tags(region, rule_arn) if rule_arn else None,
            )
    except Exception as e:
        writer.add_error(region=region, source='account (eventbridge rules)', message=e)

    try:
        recorders = get_config_recorders(region)
    except Exception as e:
        writer.add_error(region=region, source='account (config recorders)', message=e)
        recorders = []
    for recorder in recorders:
        name = recorder.get('name', region)
        writer.add_resource(
            resource_type='config_recorder', region=region, resource_id=name,
            resource_name=name, raw=recorder,
        )

    try:
        channels = get_config_delivery_channels(region)
    except Exception as e:
        writer.add_error(region=region, source='account (config delivery channels)', message=e)
        channels = []
    for channel in channels:
        name = channel.get('name', region)
        writer.add_resource(
            resource_type='config_delivery_channel', region=region, resource_id=name,
            resource_name=name, raw=channel,
        )

    try:
        aggregators = get_config_aggregators(region)
    except Exception as e:
        writer.add_error(region=region, source='account (config aggregators)', message=e)
        aggregators = []
    for agg in aggregators:
        name = agg.get('ConfigurationAggregatorName', region)
        agg_arn = agg.get('ConfigurationAggregatorArn')
        writer.add_resource(
            resource_type='config_aggregator', region=region, resource_id=name,
            resource_name=name, raw=agg,
            tags=get_config_aggregator_tags(region, agg_arn) if agg_arn else None,
        )

    try:
        detectors = get_guardduty_detectors(region)
    except Exception as e:
        writer.add_error(region=region, source='account (guardduty detectors)', message=e)
        detectors = []
    for detector in detectors:
        detector_id = detector['DetectorId']
        writer.add_resource(
            resource_type='guardduty_detector', region=region, resource_id=detector_id,
            resource_name=detector_id, raw=detector, tags=detector.get('Tags'),
        )

    try:
        analyzers = get_access_analyzers(region)
    except Exception as e:
        writer.add_error(region=region, source='account (access analyzers)', message=e)
        analyzers = []
    for analyzer in analyzers:
        arn = analyzer.get('arn')
        writer.add_resource(
            resource_type='access_analyzer', region=region, resource_id=arn,
            resource_name=analyzer.get('name', arn), raw=analyzer, tags=analyzer.get('tags'),
        )

    try:
        log_groups = get_log_groups(region)
    except Exception as e:
        writer.add_error(region=region, source='account (log groups)', message=e)
        log_groups = []
    for lg in log_groups:
        name = lg.get('logGroupName')
        arn = lg.get('arn') or lg.get('logGroupArn')
        writer.add_resource(
            resource_type='cloudwatch_log_group', region=region, resource_id=name,
            resource_name=name, raw=lg,
            tags=get_log_group_tags(region, arn) if arn else None,
        )

    try:
        xray_cfg = get_xray_encryption_config(region)
    except Exception as e:
        writer.add_error(region=region, source='account (xray encryption config)', message=e)
        xray_cfg = None
    if xray_cfg is not None:
        writer.add_resource(
            resource_type='xray_encryption_config', region=region, resource_id=f'xray-{region}',
            resource_name=f'xray-{region}', raw=xray_cfg,
        )
