"""ECR gathering — repositories (merged with their repository policy) and
the registry-wide image-scanning configuration.

`get_repositories` (describe_repositories) and `get_registry_scan_rules`
(get_registry_scanning_configuration) are the two pure fetchers.
`get_repository_policy` IS raw per-resource data (the repository's resource
policy document), so it's merged into each repository's raw record the same
fused-fetch pattern as s3.py's per-bucket policy fetch; image-scan,
mutable-tag, customer-managed-key, and public/cross-account policy
evaluation is left server-side.

The registry scan rules are gathered once per region as their own
`ecr_registry_scan_config` record (matching wildcard-filter rules against
each repository name is evaluation, left server-side) rather than
pre-computing which repos are "covered."
"""

import json

import boto3


def get_repositories(region):
    ecr = boto3.client('ecr', region_name=region)
    repos = []
    for page in ecr.get_paginator('describe_repositories').paginate():
        repos.extend(page.get('repositories', []))
    return repos


def get_registry_scan_rules(region):
    ecr = boto3.client('ecr', region_name=region)
    try:
        resp = ecr.get_registry_scanning_configuration()
        return resp.get('scanningConfiguration', {}).get('rules', [])
    except Exception:
        return []


def get_repository_policy(region, name):
    ecr = boto3.client('ecr', region_name=region)
    try:
        resp = ecr.get_repository_policy(repositoryName=name)
        return json.loads(resp.get('policyText', '{}'))
    except Exception:
        return None


def gather(region, writer):
    scan_rules = get_registry_scan_rules(region)
    writer.add_resource(
        resource_type='ecr_registry_scan_config', region=region,
        resource_id=f'ecr-scanconfig-{region}', resource_name=f'ecr-scanconfig-{region}',
        raw={'rules': scan_rules},
    )

    for repo in get_repositories(region):
        name = repo.get('repositoryName', '')
        raw = dict(repo)
        raw['_RepositoryPolicy'] = get_repository_policy(region, name)
        writer.add_resource(
            resource_type='ecr_repository',
            region=region,
            resource_id=repo.get('repositoryArn', ''),
            resource_name=name,
            raw=raw,
        )
