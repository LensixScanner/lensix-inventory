"""Cloud Logging / Monitoring gathering — log buckets, log sinks,
log-based metrics, and alert policies.

Every call here is a static configuration listing (`buckets().list()`,
`sinks().list()`, `metrics().list()`, `alertPolicies().list()`) — despite
the module name, none of it is the time-windowed telemetry this tool
otherwise excludes (e.g. reading actual log entries over a date range, or
metric time-series data points); it's metric/alert *definitions*, which
are as much "resource configuration" as a CloudWatch alarm definition is
in AWS. So unlike, say, EC2's CPU-utilization checks, nothing in this
module needed to be skipped for that reason.

Missing/exempted audit logging, unencrypted log buckets, missing/dangling
log sinks, and "no alert policy exists for this class of log-based metric"
evaluation (cross-referencing metrics() against alertPolicies() by filter-
string substring match) is left server-side — log-based metrics and alert
policies are each uploaded as their own plain resource listing and Lensix
can recompute the same substring-match correlation server-side, the same
way aws/sg.py's docstring describes recomputing "is this SG referenced
anywhere" from the union of uploaded resources.

Audit-config data (`auditConfigs` from the project's IAM policy) IS
gathered here now too, via iam.py's own get_iam_policy() (reused
directly, not the whole iam.py gather(), which would also fetch service
accounts and workload identity pools this module doesn't need) — merged
into the SAME `iam_policy` resource shape iam.py's own gather() produces
(same resource_id: an upload that runs both modules together, as
lensix-inventory-light's own full-account run() does, ends up with one
iam_policy record either way; a live scan of just this module gets its
own copy, an accepted duplicate live call — same tradeoff lb.py's dual
target_group gather and account.py's cross-region root-usage re-fetch
already established).

Whether a log sink's destination bucket still exists IS checked here too
now (get_destination_bucket_exists) — a live existence-check call per
sink with a storage.googleapis.com destination, merged into that sink's
own raw['_DestinationBucketExists'] (True/False/None — None means
"inconclusive," e.g. a 403 meaning the bucket exists but this credential
can't see it, treated the same as the live check always has: not a
dangling-sink finding).
"""

from googleapiclient import discovery
from googleapiclient.errors import HttpError

from .iam import get_iam_policy


def get_log_buckets(logging_api, project_id):
    resp = logging_api.projects().locations().buckets().list(
        parent=f'projects/{project_id}/locations/-'
    ).execute()
    return resp.get('buckets', [])


def get_log_sinks(logging_api, project_id):
    resp = logging_api.projects().sinks().list(parent=f'projects/{project_id}').execute()
    return resp.get('sinks', [])


def get_log_based_metrics(logging_api, project_id):
    resp = logging_api.projects().metrics().list(parent=f'projects/{project_id}').execute()
    return resp.get('metrics', [])


def get_alert_policies(monitoring, project_id):
    resp = monitoring.projects().alertPolicies().list(name=f'projects/{project_id}').execute()
    return resp.get('alertPolicies', [])


def get_destination_bucket_exists(storage_api, destination):
    """True/False if `destination` is a storage.googleapis.com/<bucket>
    sink target and the existence check was conclusive (404 -> False,
    everything else that succeeds or comes back 403 -> True/None); None
    immediately, no call at all, if `destination` isn't a GCS bucket.
    A 403 (bucket exists, this credential just can't see it) returns
    None rather than raising — inconclusive, but not itself a failure.
    Any other error raises, for gather() to isolate and record."""
    if not destination.startswith('storage.googleapis.com/'):
        return None
    bucket_name = destination[len('storage.googleapis.com/'):]
    try:
        storage_api.buckets().get(bucket=bucket_name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        if e.resp.status == 403:
            return None
        raise


def gather(project_id, credentials, writer):
    logging_api = discovery.build('logging', 'v2', credentials=credentials)
    monitoring = discovery.build('monitoring', 'v3', credentials=credentials)
    crm = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
    storage_api = discovery.build('storage', 'v1', credentials=credentials)

    try:
        for bucket in get_log_buckets(logging_api, project_id):
            name = bucket.get('name', '')
            writer.add_resource(
                resource_type='log_bucket',
                region='global',
                resource_id=name,
                resource_name=name.split('/')[-1],
                raw=bucket,
            )
    except Exception as e:
        writer.add_error(region='global', source='log_bucket', message=e)

    try:
        for sink in get_log_sinks(logging_api, project_id):
            name = sink.get('name', '')
            raw = dict(sink)
            try:
                raw['_DestinationBucketExists'] = get_destination_bucket_exists(storage_api, sink.get('destination', ''))
            except Exception as e:
                raw['_DestinationBucketExists'] = None
                writer.add_error(region='global', source=f'log_sink (destination bucket:{name})', message=e)
            writer.add_resource(
                resource_type='log_sink',
                region='global',
                resource_id=name,
                resource_name=name.split('/')[-1],
                raw=raw,
            )
    except Exception as e:
        writer.add_error(region='global', source='log_sink', message=e)

    try:
        for metric in get_log_based_metrics(logging_api, project_id):
            name = metric.get('name', '')
            writer.add_resource(
                resource_type='log_based_metric',
                region='global',
                resource_id=name,
                resource_name=name.split('/')[-1],
                raw=metric,
            )
    except Exception as e:
        writer.add_error(region='global', source='log_based_metric', message=e)

    try:
        for policy in get_alert_policies(monitoring, project_id):
            name = policy.get('name', '')
            writer.add_resource(
                resource_type='alert_policy',
                region='global',
                resource_id=name,
                resource_name=policy.get('displayName', name.split('/')[-1]),
                raw=policy,
            )
    except Exception as e:
        writer.add_error(region='global', source='alert_policy', message=e)

    # --- Project IAM policy (audit configs) — reused from iam.py's own
    # get_iam_policy(), same resource shape its own gather() produces. ---
    try:
        policy = get_iam_policy(crm, project_id)
        writer.add_resource(
            resource_type='iam_policy',
            region='global',
            resource_id=f'{project_id}/iam',
            resource_name=project_id,
            raw=policy,
        )
    except Exception as e:
        writer.add_error(region='global', source='iam_policy', message=e)
