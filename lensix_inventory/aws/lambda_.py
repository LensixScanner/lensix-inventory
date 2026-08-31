"""Lambda gathering.

`list_functions` already returns everything most evaluation needs in one
call (TracingConfig, VpcConfig, Runtime, Role, Environment.Variables, ...) —
no extra fan-out needed.

Environment variable VALUES are scanned locally for secrets and then
stripped before upload (see common/secrets.py) — only the scan result and
the variable NAMES (never secrets themselves) are kept, since function
environment variables are a common place for hardcoded credentials to end
up.

Per-function secondary lookups are merged in, each isolated so one
function's failure doesn't affect its own other lookup or any other
function:
  - `_ResourcePolicy` — the function's resource-based policy
    (get_policy(), parsed), used for both nosourcearn/public evaluation.
    None when the function has no resource policy at all
    (ResourceNotFoundException) or the fetch/parse otherwise failed —
    both cases mean "nothing to evaluate," not "policy is empty."
  - `_HasLogGroup` — whether `/aws/lambda/<name>` exists in CloudWatch
    Logs.
  - `_RoleExists` / `_RoleHasAdminPrivileges` — the execution role's
    existence and whether any of its attached/inline policies grant
    wildcard admin (Action '*'/'iam:*' + Resource '*'). This IS Lambda-
    specific rather than IAM-inventory data (it's evaluated per function,
    not per role, and only for roles Lambda functions actually
    reference) — deliberately gathered here rather than folded into
    lensix_inventory.aws.account.py's iam_role resource, and cached by
    role name across functions within one gather() call so N functions
    sharing one role only cost one IAM fan-out, not N.
"""

import json

import boto3

from ..common.secrets import scan_text_for_secrets

_ADMIN_POLICY_ARN = 'arn:aws:iam::aws:policy/AdministratorAccess'


def get_functions(region):
    lambda_client = boto3.client('lambda', region_name=region)
    functions = []
    for page in lambda_client.get_paginator('list_functions').paginate():
        functions.extend(page['Functions'])
    return functions


def get_function_tags(region, function_arn):
    """Lambda tags aren't included in list_functions()'s own response —
    they need a separate call, keyed by the function's ARN (not its
    name). Returns a flat {key: value} dict (Lambda's ListTags already
    returns that shape, unlike most other AWS tag APIs), or {} on
    failure — no tags is not worth failing gather() over."""
    lambda_client = boto3.client('lambda', region_name=region)
    try:
        return lambda_client.list_tags(Resource=function_arn).get('Tags', {})
    except Exception:
        return {}


def get_resource_policy(region, function_name):
    lambda_client = boto3.client('lambda', region_name=region)
    try:
        resp = lambda_client.get_policy(FunctionName=function_name)
    except lambda_client.exceptions.ResourceNotFoundException:
        return None
    try:
        return json.loads(resp['Policy'])
    except (KeyError, ValueError):
        return None


def get_has_log_group(region, function_name):
    logs_client = boto3.client('logs', region_name=region)
    resp = logs_client.describe_log_groups(logGroupNamePrefix=f'/aws/lambda/{function_name}')
    exact = f'/aws/lambda/{function_name}'
    return any(g.get('logGroupName') == exact for g in resp.get('logGroups', []))


def _role_exists(iam, role_name):
    try:
        iam.get_role(RoleName=role_name)
        return True
    except iam.exceptions.NoSuchEntityException:
        return False


def _doc_has_wildcard_admin(doc):
    for stmt in (doc or {}).get('Statement', []):
        if stmt.get('Effect') != 'Allow':
            continue
        action = stmt.get('Action', [])
        resource = stmt.get('Resource', [])
        if isinstance(action, str):
            action = [action]
        if isinstance(resource, str):
            resource = [resource]
        if ('*' in action or 'iam:*' in action) and '*' in resource:
            return True
    return False


def _role_has_admin(iam, role_name):
    attached = iam.list_attached_role_policies(RoleName=role_name).get('AttachedPolicies', [])
    for p in attached:
        if p.get('PolicyArn') == _ADMIN_POLICY_ARN:
            return True
        try:
            policy_doc = iam.get_policy_version(
                PolicyArn=p['PolicyArn'],
                VersionId=iam.get_policy(PolicyArn=p['PolicyArn'])['Policy']['DefaultVersionId'],
            )['PolicyVersion']['Document']
        except Exception:
            continue
        if _doc_has_wildcard_admin(policy_doc):
            return True

    inline_names = iam.list_role_policies(RoleName=role_name).get('PolicyNames', [])
    for pname in inline_names:
        try:
            inline_doc = iam.get_role_policy(RoleName=role_name, PolicyName=pname)['PolicyDocument']
        except Exception:
            continue
        if _doc_has_wildcard_admin(inline_doc):
            return True
    return False


def _redact_environment(fn):
    """Returns (env_var_names_only, secret_scan_hits) — the raw values
    themselves are discarded immediately after being scanned."""
    variables = fn.get('Environment', {}).get('Variables', {}) or {}
    hits = []
    for value in variables.values():
        hits.extend(scan_text_for_secrets(str(value)))
    return sorted(variables.keys()), sorted(set(hits))


def gather(region, writer):
    iam = boto3.client('iam')
    role_exists_cache = {}
    role_admin_cache = {}

    for fn in get_functions(region):
        var_names, secret_hits = _redact_environment(fn)
        name = fn['FunctionName']

        raw = dict(fn)
        if 'Environment' in raw:
            raw['Environment'] = {**raw['Environment'], 'VariableNames': var_names}
            raw['Environment'].pop('Variables', None)

        try:
            raw['_ResourcePolicy'] = get_resource_policy(region, name)
        except Exception as e:
            raw['_ResourcePolicy'] = None
            writer.add_error(region=region, source=f'lambda (resource policy:{name})', message=e)

        try:
            raw['_HasLogGroup'] = get_has_log_group(region, name)
        except Exception as e:
            raw['_HasLogGroup'] = None
            writer.add_error(region=region, source=f'lambda (log group:{name})', message=e)

        role_arn = fn.get('Role', '')
        role_name = role_arn.split(':role/')[-1] if role_arn else None
        if role_name:
            if role_name not in role_exists_cache:
                try:
                    role_exists_cache[role_name] = _role_exists(iam, role_name)
                except Exception as e:
                    role_exists_cache[role_name] = None
                    writer.add_error(region=region, source=f'lambda (role exists:{role_name})', message=e)
            raw['_RoleExists'] = role_exists_cache[role_name]

            if role_name not in role_admin_cache:
                try:
                    role_admin_cache[role_name] = _role_has_admin(iam, role_name)
                except Exception as e:
                    role_admin_cache[role_name] = None
                    writer.add_error(region=region, source=f'lambda (role admin check:{role_name})', message=e)
            raw['_RoleHasAdminPrivileges'] = role_admin_cache[role_name]
        else:
            raw['_RoleExists'] = None
            raw['_RoleHasAdminPrivileges'] = None

        vpc_id = fn.get('VpcConfig', {}).get('VpcId') or None

        writer.add_resource(
            resource_type='lambda_function',
            region=region,
            resource_id=fn['FunctionArn'],
            resource_name=name,
            scope_id=vpc_id,
            raw=raw,
            secret_scan_hits=secret_hits,
            tags=get_function_tags(region, fn['FunctionArn']),
        )
