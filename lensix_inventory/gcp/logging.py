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

Audit-config data (`auditConfigs` from the project's IAM policy) is
deliberately NOT re-fetched here even though audit-logging evaluation
needs it — iam.py already gathers the exact same getIamPolicy response as
its `iam_policy` resource, so fetching it a second time here would just
upload the same data twice under a different resource_type. Lensix can
evaluate both this module's and iam.py's audit-logging checks from that
one `iam_policy` record.

Whether a log sink's destination bucket still exists requires a live
existence-check API call against the destination — that's check-time
verification, not resource gathering, so it isn't done here; the sink's
raw `destination` field is uploaded as-is and Lensix can verify it
server-side if desired.
"""

from googleapiclient import discovery


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


def gather(project_id, credentials, writer):
    logging_api = discovery.build('logging', 'v2', credentials=credentials)
    monitoring = discovery.build('monitoring', 'v3', credentials=credentials)

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
            writer.add_resource(
                resource_type='log_sink',
                region='global',
                resource_id=name,
                resource_name=name.split('/')[-1],
                raw=sink,
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
