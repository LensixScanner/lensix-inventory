"""IAM gathering — users.

IAM is global, so this module never loops regions, mirroring s3.py's
`gather(writer)` pattern instead of `gather(region, writer)`.

Access key age, last console login, MFA status, and similar per-user
security signals are all derived from the IAM credential report — a
stateful generate-then-poll workflow (generate_credential_report, then
poll get_credential_report until ready) rather than a simple
list/describe call. The actual live fetch lives in
common/credential_report.py, shared with account.py's own root-row need
for the identical report — see that module's docstring for why. It's
fetched at most once per gather() call (not once per user — the report
already covers every IAM user in the account in one CSV, and a caller
gathering account.py's gather_global() in the same process can pass its
own already-fetched copy in via `credential_report_content` instead of
letting this module fetch a second, redundant copy) and each user's own
row is merged into that user's raw record as `_CredentialReport` (None
if the report never became ready, or if this particular user has no row
— e.g. `<root_account>`, which isn't a `list_users()` result and is
dropped rather than merged into anything).

Per-user fan-out (attached policies, group membership, the escalation
simulation, tags — 4 API calls per user) runs concurrently via
common/concurrency.py's parallel_map, the same pattern
account.py uses for its own per-resource loops — these are independent,
read-only calls per user, so there's no ordering dependency between
users to preserve.

This is also the sole owner of the `iam_user` resource type in this tool
(account.py covers every other IAM/account-security resource type, but
deliberately not this one, to avoid gathering the same users twice) —
this module folds in list_attached_user_policies, list_groups_for_user
(needed for direct-policy and group-membership evaluation), the
credential report row, and a privilege-escalation policy simulation
(`_EscalationActions` — see get_escalation_actions()'s own docstring) per
user, matching s3.py's fused fan-out pattern.
"""

import boto3

from lensix_inventory.common.concurrency import parallel_map
from lensix_inventory.common.credential_report import fetch_credential_report_content, parse_credential_report_rows

# Actions that allow privilege escalation if simulate_principal_policy
# says a user can perform them — see get_escalation_actions()'s own
# docstring.
ESCALATION_ACTIONS = [
    'iam:CreatePolicy',
    'iam:CreatePolicyVersion',
    'iam:SetDefaultPolicyVersion',
    'iam:PutUserPolicy',
    'iam:AttachUserPolicy',
    'iam:AttachGroupPolicy',
    'iam:AttachRolePolicy',
]


def get_users():
    iam = boto3.client('iam')
    users = []
    for page in iam.get_paginator('list_users').paginate():
        users.extend(page['Users'])
    return users


def get_attached_user_policies(username):
    iam = boto3.client('iam')
    policies = []
    for page in iam.get_paginator('list_attached_user_policies').paginate(UserName=username):
        policies.extend(page['AttachedPolicies'])
    return policies


def get_groups_for_user(username):
    iam = boto3.client('iam')
    groups = []
    for page in iam.get_paginator('list_groups_for_user').paginate(UserName=username):
        groups.extend(page['Groups'])
    return groups


def get_escalation_actions(arn):
    """Runs a live IAM policy simulation (simulate_principal_policy) for
    one user against a fixed list of privilege-escalation-capable actions
    (ESCALATION_ACTIONS) and returns just the ones it says are allowed.
    This IS a live "what-if" evaluation — not a listing of the user's own
    resource state the way every other fetch in this tool is — but the
    RESULT is deterministic given the account's current policies, so
    running it once here at gather time and shipping the (small) allowed-
    action list is equivalent to a live check re-running the same
    simulation later, as long as the data is used "reasonably fresh" like
    everything else this tool gathers."""
    iam = boto3.client('iam')
    resp = iam.simulate_principal_policy(
        PolicySourceArn=arn,
        ActionNames=ESCALATION_ACTIONS,
        ResourceArns=['*'],
    )
    return [
        r['EvalActionName'] for r in resp.get('EvaluationResults', [])
        if r.get('EvalDecision') == 'allowed'
    ]


def get_user_tags(username):
    """list_users' own response doesn't include tags — IAM's own
    separate, paginated list_user_tags call. Returns [] on failure."""
    iam = boto3.client('iam')
    tags = []
    try:
        kwargs = {'UserName': username}
        while True:
            resp = iam.list_user_tags(**kwargs)
            tags.extend(resp.get('Tags', []))
            if not resp.get('IsTruncated'):
                break
            kwargs['Marker'] = resp.get('Marker')
    except Exception:
        return []
    return tags


def get_credential_report_by_username(credential_report_content=None):
    """`credential_report_content`, when given, is an already-fetched
    report (e.g. account.py's gather_global() fetched one for the root
    row in the same process) — parsed directly, skipping this module's
    own live fetch. Omitted (the default), this fetches its own copy, so
    this function stays independently callable."""
    content = credential_report_content if credential_report_content is not None else fetch_credential_report_content()
    rows = parse_credential_report_rows(content)
    # <root_account> has its own row but is never a list_users() result —
    # nothing to merge it into.
    return {row['user']: row for row in rows if row.get('user') != '<root_account>'}


def _gather_one_user(user):
    """Fetches the per-user fan-out (policies, groups, escalation sim,
    tags) for one user, returning (raw, tags, errors) rather than raising
    or touching the writer directly — this runs inside parallel_map's
    thread pool, and InventoryWriter isn't thread-safe, so every thread
    must finish before anything gets written. `errors` is a list (not a
    single value) since the two try/excepts below are independent and
    both can fail for the same user."""
    username = user['UserName']
    arn = user['Arn']
    raw = dict(user)
    errors = []

    try:
        raw['_AttachedPolicies'] = get_attached_user_policies(username)
        raw['_Groups'] = get_groups_for_user(username)
    except Exception as e:
        errors.append((f'iam_user:{arn}', e))
        raw.setdefault('_AttachedPolicies', [])
        raw.setdefault('_Groups', [])

    try:
        raw['_EscalationActions'] = get_escalation_actions(arn)
    except Exception as e:
        errors.append((f'iam_user (escalation simulation:{arn})', e))
        raw['_EscalationActions'] = []

    return raw, get_user_tags(username), errors


def gather(writer, credential_report_content=None):
    try:
        report_by_username = get_credential_report_by_username(credential_report_content)
    except Exception as e:
        writer.add_error(region='global', source='iam_user (credential report)', message=e)
        report_by_username = {}

    users = get_users()
    for raw, tags, errors in parallel_map(_gather_one_user, users):
        for source, e in errors:
            writer.add_error(region='global', source=source, message=e)
        raw['_CredentialReport'] = report_by_username.get(raw['UserName'])
        writer.add_resource(
            resource_type='iam_user',
            region='global',
            resource_id=raw['Arn'],
            resource_name=raw['UserName'],
            raw=raw,
            tags=tags,
        )
