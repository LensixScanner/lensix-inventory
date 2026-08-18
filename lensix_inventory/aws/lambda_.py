"""Lambda gathering.

`list_functions` already returns everything most evaluation needs in one
call (TracingConfig, VpcConfig, Runtime, Role, Environment.Variables, ...) —
no extra fan-out needed.

Environment variable VALUES are scanned locally for secrets and then
stripped before upload (see common/secrets.py) — only the scan result and
the variable NAMES (never secrets themselves) are kept, since function
environment variables are a common place for hardcoded credentials to end
up.

Per-function secondary lookups (resource policy via get_policy, log group
existence, IAM role validity/admin-ness) are NOT yet gathered here —
flagged as a follow-up, not silently dropped.
"""

import boto3

from ..common.secrets import scan_text_for_secrets


def get_functions(region):
    lambda_client = boto3.client('lambda', region_name=region)
    functions = []
    for page in lambda_client.get_paginator('list_functions').paginate():
        functions.extend(page['Functions'])
    return functions


def _redact_environment(fn):
    """Returns (env_var_names_only, secret_scan_hits) — the raw values
    themselves are discarded immediately after being scanned."""
    variables = fn.get('Environment', {}).get('Variables', {}) or {}
    hits = []
    for value in variables.values():
        hits.extend(scan_text_for_secrets(str(value)))
    return sorted(variables.keys()), sorted(set(hits))


def gather(region, writer):
    for fn in get_functions(region):
        var_names, secret_hits = _redact_environment(fn)

        raw = dict(fn)
        if 'Environment' in raw:
            raw['Environment'] = {**raw['Environment'], 'VariableNames': var_names}
            raw['Environment'].pop('Variables', None)

        vpc_id = fn.get('VpcConfig', {}).get('VpcId') or None

        writer.add_resource(
            resource_type='lambda_function',
            region=region,
            resource_id=fn['FunctionArn'],
            resource_name=fn['FunctionName'],
            scope_id=vpc_id,
            raw=raw,
            secret_scan_hits=secret_hits,
        )
