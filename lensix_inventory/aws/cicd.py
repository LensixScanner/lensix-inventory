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
"""

import boto3

from ..common.secrets import scan_text_for_secrets


def get_repositories(region):
    cc = boto3.client('codecommit', region_name=region)
    repos = []
    for page in cc.get_paginator('list_repositories').paginate():
        repos.extend(page['repositories'])
    return repos


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
    for repo in get_repositories(region):
        writer.add_resource(
            resource_type='codecommit_repo',
            region=region,
            resource_id=repo.get('repositoryId', repo.get('repositoryName', '')),
            resource_name=repo.get('repositoryName', ''),
            raw=repo,
        )

    for project in get_codebuild_projects(region):
        raw, secret_hits = _redact_project(project)
        writer.add_resource(
            resource_type='codebuild_project',
            region=region,
            resource_id=project.get('arn', project.get('name', '')),
            resource_name=project.get('name', ''),
            raw=raw,
            secret_scan_hits=secret_hits,
        )
