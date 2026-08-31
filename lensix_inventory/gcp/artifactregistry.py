"""Artifact Registry gathering — repositories across all regions in one
call (same location-wildcard convenience Cloud Run's API supports), IAM
policy merged into each repository's raw record.

Vulnerability scanning enablement (`artifactregistry_novulnscan`) and
cleanup-policy presence (`artifactregistry_nocleanup`) are both fields
already present on the repository object itself
(`vulnerabilityScanningConfig`, `cleanupPolicies`) — no separate API call
needed for either, unlike public-access evaluation which needs its own
getIamPolicy() fetch. Public-access evaluation is left server-side.
"""

from googleapiclient import discovery


def get_repositories(ar, project_id):
    repos = []
    request = ar.projects().locations().repositories().list(parent=f'projects/{project_id}/locations/-')
    while request is not None:
        resp = request.execute()
        repos.extend(resp.get('repositories', []))
        request = ar.projects().locations().repositories().list_next(previous_request=request, previous_response=resp)
    return repos


def get_iam_policy(ar, repo_name):
    resp = ar.projects().locations().repositories().getIamPolicy(resource=repo_name).execute()
    return resp.get('bindings', [])


def _region_from_repo_name(name):
    """projects/{project}/locations/{location}/repositories/{repo}"""
    parts = name.split('/')
    try:
        loc_idx = parts.index('locations')
        return parts[loc_idx + 1]
    except (ValueError, IndexError):
        return 'global'


def gather(project_id, credentials, writer):
    ar = discovery.build('artifactregistry', 'v1', credentials=credentials)

    try:
        repos = get_repositories(ar, project_id)
    except Exception as e:
        writer.add_error(region='global', source='artifactregistry_repository', message=e)
        return

    for repo in repos:
        name = repo.get('name', '')
        region = _region_from_repo_name(name)

        raw = dict(repo)
        try:
            raw['_IamPolicyBindings'] = get_iam_policy(ar, name)
        except Exception as e:
            writer.add_error(region=region, source=f'artifactregistry_repository:{name}', message=e)

        writer.add_resource(
            resource_type='artifactregistry_repository',
            region=region,
            resource_id=name,
            resource_name=name.split('/')[-1] if name else name,
            raw=raw,
            tags=raw.get('labels'),
        )
