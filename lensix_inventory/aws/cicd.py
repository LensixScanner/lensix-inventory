"""CI/CD gathering — CodeCommit repositories and CodeBuild projects
(environment variable values redacted).

`get_repositories` (list_repositories) and `get_codebuild_projects`
(list_projects + batch_get_projects) are the two pure fetchers;
encryption and source-configuration evaluation is left server-side. Every
CodeCommit repo also carries an unconditional "this resource type is
deprecated" finding, which needs no data beyond the repo's existence,
already captured by gathering the resource.

**Secrets exception**: a CodeBuild project's `environment.environmentVariables`
supports a `PLAINTEXT` type where `value` is a literal string the project
author typed in directly — exactly the kind of field
`aws/lambda_.py`'s `_redact_environment` exists to catch, and just as
commonly used to paste a real API key/token as Lambda's own env vars are.
Every variable's `value` (PLAINTEXT or not — a Parameter Store/Secrets
Manager `value` is just a reference name, harmless to scan and to drop) is
scanned locally then discarded; only `name`/`type` and the scan result are
kept.

CodeBuild projects carry their own `tags` inline (lowercase {'key','value'}
list). CodeCommit repos need two extra calls per repo just to get there —
see get_repository_tags()'s own docstring for why.
"""

import boto3

from ..common.secrets import scan_text_for_secrets


def get_repositories(region):
    cc = boto3.client('codecommit', region_name=region)
    repos = []
    for page in cc.get_paginator('list_repositories').paginate():
        repos.extend(page['repositories'])
    return repos


def get_repository_tags(region, repository_name):
    """CodeCommit's list_repositories doesn't return an ARN (or tags) at
    all — get_repository is needed first just to obtain the ARN
    list_tags_for_resource requires (constructing it by hand would need
    account_id, which this module's gather(region, writer) signature
    doesn't carry — same gap documented in glue.py/athena.py). Returns {}
    on any failure (including get_repository itself)."""
    cc = boto3.client('codecommit', region_name=region)
    try:
        arn = cc.get_repository(repositoryName=repository_name)['repositoryMetadata']['Arn']
        tags = {}
        kwargs = {'resourceArn': arn}
        while True:
            resp = cc.list_tags_for_resource(**kwargs)
            tags.update(resp.get('tags', {}))
            next_token = resp.get('nextToken')
            if not next_token:
                break
            kwargs['nextToken'] = next_token
        return tags
    except Exception:
        return {}


def get_codebuild_projects(region):
    cb = boto3.client('codebuild', region_name=region)
    names = []
    kwargs = {}
    while True:
        resp = cb.list_projects(**kwargs)
        names.extend(resp.get('projects', []))
        next_token = resp.get('nextToken')
        if not next_token:
            break
        kwargs['nextToken'] = next_token

    projects = []
    for i in range(0, len(names), 100):
        batch = names[i:i + 100]
        projects.extend(cb.batch_get_projects(names=batch)['projects'])
    return projects


def _redact_project(project):
    """Returns (redacted_project, secret_scan_hits) — each environment
    variable's `value` is scanned locally then discarded; only `name` and
    `type` (PLAINTEXT/PARAMETER_STORE/SECRETS_MANAGER) remain."""
    raw = dict(project)
    hits = []
    env = raw.get('environment')
    if isinstance(env, dict) and 'environmentVariables' in env:
        env = dict(env)
        redacted_vars = []
        for var in env.get('environmentVariables', []):
            hits.extend(scan_text_for_secrets(str(var.get('value', ''))))
            redacted_vars.append({'name': var.get('name', ''), 'type': var.get('type', '')})
        env['environmentVariables'] = redacted_vars
        raw['environment'] = env
    return raw, sorted(set(hits))


def gather(region, writer):
    # CodeCommit repos and CodeBuild projects are independent list calls —
    # isolate them so a failure fetching one doesn't prevent the other
    # from being gathered.
    try:
        for repo in get_repositories(region):
            name = repo.get('repositoryName', '')
            writer.add_resource(
                resource_type='codecommit_repo',
                region=region,
                resource_id=repo.get('repositoryId', name),
                resource_name=name,
                raw=repo,
                tags=get_repository_tags(region, name),
            )
    except Exception as e:
        writer.add_error(region=region, source='cicd (codecommit repos)', message=e)

    try:
        for project in get_codebuild_projects(region):
            raw, secret_hits = _redact_project(project)
            writer.add_resource(
                resource_type='codebuild_project',
                region=region,
                resource_id=project.get('arn', project.get('name', '')),
                resource_name=project.get('name', ''),
                raw=raw,
                secret_scan_hits=secret_hits,
                tags=project.get('tags'),
            )
    except Exception as e:
        writer.add_error(region=region, source='cicd (codebuild projects)', message=e)
