"""ECS gathering — clusters and task definitions (environment variable
values redacted).

`get_cluster_arns`/`describe_clusters` and `get_task_definition_families`/
`describe_task_definition` are the pure fetchers; Container Insights and
plaintext-secrets evaluation is left server-side, computed from the
uploaded data.

**Secrets exception**: environment variable *values* are exactly the kind
of field this tool's design principle (see README) requires redacting
locally, so they're scanned with `scan_text_for_secrets` before upload.
Each task definition's `containerDefinitions[].
environment` is rewritten here to variable *names* only (values discarded
immediately after being scanned), with the union of matched rule names
across all containers kept in `secret_scan_hits` — the same treatment as
lambda_.py's `_redact_environment`. `secrets` (references to Secrets
Manager/SSM Parameter Store entries, not literal values) are left intact
since they carry no literal secret text.
"""

import boto3

from ..common.secrets import scan_text_for_secrets


def get_cluster_arns(region):
    ecs = boto3.client('ecs', region_name=region)
    arns = []
    for page in ecs.get_paginator('list_clusters').paginate():
        arns.extend(page['clusterArns'])
    return arns


def describe_clusters(region, arns):
    ecs = boto3.client('ecs', region_name=region)
    clusters = []
    for i in range(0, len(arns), 100):
        batch = arns[i:i + 100]
        resp = ecs.describe_clusters(clusters=batch, include=['SETTINGS'])
        clusters.extend(resp['clusters'])
    return clusters


def get_task_definition_families(region):
    ecs = boto3.client('ecs', region_name=region)
    families = []
    for page in ecs.get_paginator('list_task_definition_families').paginate(status='ACTIVE'):
        families.extend(page['families'])
    return families


def describe_task_definition(region, family):
    ecs = boto3.client('ecs', region_name=region)
    return ecs.describe_task_definition(taskDefinition=family)['taskDefinition']


def _redact_task_def(task_def):
    """Returns (redacted_task_def, secret_scan_hits) — container env var
    values are scanned locally then discarded; only variable names remain."""
    raw = dict(task_def)
    hits = []
    containers = []
    for container in raw.get('containerDefinitions', []):
        c = dict(container)
        if 'environment' in c:
            env_names = []
            for env in c['environment']:
                env_names.append(env.get('name', ''))
                hits.extend(scan_text_for_secrets(str(env.get('value', ''))))
            c['environment'] = env_names
        containers.append(c)
    raw['containerDefinitions'] = containers
    return raw, sorted(set(hits))


def gather(region, writer):
    try:
        arns = get_cluster_arns(region)
    except Exception as e:
        writer.add_error(region=region, source='ecs_cluster', message=e)
        arns = []

    if arns:
        try:
            clusters = describe_clusters(region, arns)
        except Exception as e:
            writer.add_error(region=region, source='ecs_cluster', message=e)
            clusters = []
        for cluster in clusters:
            cluster_arn = cluster['clusterArn']
            writer.add_resource(
                resource_type='ecs_cluster', region=region, resource_id=cluster_arn,
                resource_name=cluster_arn.split('/')[-1], raw=cluster,
            )

    try:
        families = get_task_definition_families(region)
    except Exception as e:
        writer.add_error(region=region, source='ecs_task_definition', message=e)
        families = []

    for family in families:
        try:
            task_def = describe_task_definition(region, family)
        except Exception as e:
            writer.add_error(region=region, source=f'ecs_task_definition:{family}', message=e)
            continue
        task_arn = task_def.get('taskDefinitionArn', family)
        revision = task_def.get('revision', '')
        raw, secret_hits = _redact_task_def(task_def)
        writer.add_resource(
            resource_type='ecs_task_definition',
            region=region,
            resource_id=task_arn,
            resource_name=f'{family}:{revision}',
            raw=raw,
            secret_scan_hits=secret_hits,
        )
