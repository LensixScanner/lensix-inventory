"""Cloud Functions (v2) gathering — one raw record per function, IAM policy
merged in.

`projects.locations.functions.list` with a wildcard location
(`locations/-`) already returns everything most evaluation needs in one
call (serviceConfig, buildConfig, ...); getIamPolicy is the one fan-out
call (needed for public-invocation evaluation), merged into each
function's raw record the same way aws/s3.py merges per-bucket sub-API
calls. HTTPS enforcement, public invocation, deprecated runtimes, default
service account, VPC connector, and ingress-setting evaluation is left
server-side.

Secrets exception (same treatment as aws/lambda_.py's Lambda environment
variables): Cloud Function environment variable VALUES are free text that
can carry hardcoded credentials. Values are scanned locally for secrets
and stripped before upload; only the variable NAMES and the scan result
are kept. Covers both `serviceConfig.environmentVariables` (runtime env
vars) AND `buildConfig.environmentVariables` (build-time env vars, e.g. a
private-registry token needed only while building the function).
"""

from googleapiclient import discovery

from ..common.secrets import scan_text_for_secrets


def _region_from_name(fn_name):
    """Extract region from a Cloud Functions v2 resource name.
    Format: projects/<project>/locations/<region>/functions/<name>
    """
    parts = fn_name.split('/')
    try:
        loc_idx = parts.index('locations')
        return parts[loc_idx + 1]
    except (ValueError, IndexError):
        return 'global'


def _redact_environment(env_vars):
    """Returns (var_names_only, secret_scan_hits) — the raw values are
    discarded immediately after being scanned."""
    env_vars = env_vars or {}
    hits = []
    for value in env_vars.values():
        hits.extend(scan_text_for_secrets(value or ''))
    return sorted(env_vars.keys()), sorted(set(hits))


def get_functions(cf, project_id):
    functions = []
    parent = f'projects/{project_id}/locations/-'
    request = cf.projects().locations().functions().list(parent=parent)
    while request is not None:
        resp = request.execute()
        functions.extend(resp.get('functions', []))
        page_token = resp.get('nextPageToken')
        request = cf.projects().locations().functions().list(parent=parent, pageToken=page_token) if page_token else None
    return functions


def get_function_iam_policy(cf, fn_name):
    resp = cf.projects().locations().functions().getIamPolicy(resource=fn_name).execute()
    return resp.get('bindings', [])


def gather(project_id, credentials, writer):
    cf = discovery.build('cloudfunctions', 'v2', credentials=credentials)

    try:
        functions = get_functions(cf, project_id)
    except Exception as e:
        writer.add_error(region='global', source='cloud_function', message=e)
        return

    for fn in functions:
        fn_name = fn.get('name', '')
        region = _region_from_name(fn_name)
        short_name = fn_name.split('/')[-1]
        svc_cfg = fn.get('serviceConfig') or {}
        build_cfg = fn.get('buildConfig') or {}

        svc_var_names, svc_secret_hits = _redact_environment(svc_cfg.get('environmentVariables'))
        build_var_names, build_secret_hits = _redact_environment(build_cfg.get('environmentVariables'))
        secret_hits = sorted(set(svc_secret_hits) | set(build_secret_hits))

        raw = dict(fn)
        if 'serviceConfig' in raw:
            raw['serviceConfig'] = {**raw['serviceConfig'], 'environmentVariableNames': svc_var_names}
            raw['serviceConfig'].pop('environmentVariables', None)
        if 'buildConfig' in raw:
            raw['buildConfig'] = {**raw['buildConfig'], 'environmentVariableNames': build_var_names}
            raw['buildConfig'].pop('environmentVariables', None)

        try:
            raw['_IamPolicyBindings'] = get_function_iam_policy(cf, fn_name)
        except Exception as e:
            writer.add_error(region=region, source=f'cloud_function:{short_name}', message=e)

        writer.add_resource(
            resource_type='cloud_function',
            region=region,
            resource_id=fn_name,
            resource_name=short_name,
            raw=raw,
            secret_scan_hits=secret_hits,
        )
