"""Secret Manager gathering — secrets (project-level, not regional) with
their version list merged into each secret's raw record.

Rotation-policy presence (`secretmanager_norotation`) and stale-version
evaluation (`secretmanager_oldversions`) both read fields already present
on the secret/version objects this module gathers. Overly broad project-
level access (`secretmanager_broadaccess`) reads the project's IAM policy
instead — already gathered once by iam.py's own gather() as a single
`iam_policy` resource (see its own docstring for why it's gathered only
there) — so this module does not fetch it a second time; the live
scanmodule (secretmanager_checks.py) calls both gather() functions into
the same writer, same pattern monitor_checks.py (Azure) established for
reusing another module's own gather() directly.
"""

from googleapiclient import discovery


def get_secrets(sm, project_id):
    secrets = []
    request = sm.projects().secrets().list(parent=f'projects/{project_id}')
    while request is not None:
        resp = request.execute()
        secrets.extend(resp.get('secrets', []))
        request = sm.projects().secrets().list_next(previous_request=request, previous_response=resp)
    return secrets


def get_versions(sm, secret_name):
    versions = []
    request = sm.projects().secrets().versions().list(parent=secret_name)
    while request is not None:
        resp = request.execute()
        versions.extend(resp.get('versions', []))
        request = sm.projects().secrets().versions().list_next(previous_request=request, previous_response=resp)
    return versions


def gather(project_id, credentials, writer):
    sm = discovery.build('secretmanager', 'v1', credentials=credentials)

    try:
        secrets = get_secrets(sm, project_id)
    except Exception as e:
        writer.add_error(region='global', source='secretmanager_secret', message=e)
        return

    for secret in secrets:
        name = secret.get('name', '')
        raw = dict(secret)
        try:
            raw['_Versions'] = get_versions(sm, name)
        except Exception as e:
            writer.add_error(region='global', source=f'secretmanager_secret:{name}', message=e)
            raw['_Versions'] = []

        writer.add_resource(
            resource_type='secretmanager_secret',
            region='global',
            resource_id=name,
            resource_name=name.split('/')[-1] if name else name,
            raw=raw,
        )
