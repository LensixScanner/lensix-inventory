"""IAM gathering — users.

IAM is global, so this module never loops regions, mirroring s3.py's
`gather(writer)` pattern instead of `gather(region, writer)`.

Access key age, last console login, MFA status, and similar per-user
security signals are all derived from the IAM credential report — a
stateful generate-then-poll workflow (generate_credential_report, then
poll get_credential_report until ready) rather than a simple
list/describe call. It's fetched once per gather() call (not once per
user — the report already covers every IAM user in the account in one
CSV) and each user's own row is merged into that user's raw record as
`_CredentialReport` (None if the report never became ready, or if this
particular user has no row — e.g. `<root_account>`, which isn't a
`list_users()` result and is dropped rather than merged into anything).

This is also the sole owner of the `iam_user` resource type in this tool
(account.py covers every other IAM/account-security resource type, but
deliberately not this one, to avoid gathering the same users twice) —
this module folds in list_attached_user_policies, list_groups_for_user
(needed for direct-policy and group-membership evaluation), the
credential report row, and a privilege-escalation policy simulation
(`_EscalationActions` — see get_escalation_actions()'s own docstring) per
user, matching s3.py's fused fan-out pattern.
"""

import csv
import io
import time

import boto3

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


def get_credential_report():
    """Returns the raw CSV content as a string. A report generation task
    already in progress (for this account, or a concurrent gather of it)
    surfaces as LimitExceededException — expected, not a failure — so
    that falls through to polling for the report already being
    generated instead of failing outright. Raises TimeoutError if the
    report never becomes ready within 15 retries (~30s)."""
    iam = boto3.client('iam')
    try:
        iam.generate_credential_report()
    except iam.exceptions.LimitExceededException:
        pass
    for _ in range(15):
        try:
            resp = iam.get_credential_report()
            return resp['Content'].decode('utf-8')
        except iam.exceptions.CredentialReportNotReadyException:
            time.sleep(2)
    raise TimeoutError('Credential report not ready after 15 retries')


def parse_credential_report(content):
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def get_credential_report_by_username():
    content = get_credential_report()
    rows = parse_credential_report(content)
    # <root_account> has its own row but is never a list_users() result —
    # nothing to merge it into.
    return {row['user']: row for row in rows if row.get('user') != '<root_account>'}


def gather(writer):
    try:
        report_by_username = get_credential_report_by_username()
    except Exception as e:
        writer.add_error(region='global', source='iam_user (credential report)', message=e)
        report_by_username = {}

    for user in get_users():
        username = user['UserName']
        arn = user['Arn']

        raw = dict(user)
        try:
            raw['_AttachedPolicies'] = get_attached_user_policies(username)
            raw['_Groups'] = get_groups_for_user(username)
        except Exception as e:
            writer.add_error(region='global', source=f'iam_user:{arn}', message=e)
            raw.setdefault('_AttachedPolicies', [])
            raw.setdefault('_Groups', [])

        try:
            raw['_EscalationActions'] = get_escalation_actions(arn)
        except Exception as e:
            writer.add_error(region='global', source=f'iam_user (escalation simulation:{arn})', message=e)
            raw['_EscalationActions'] = []

        raw['_CredentialReport'] = report_by_username.get(username)

        writer.add_resource(
            resource_type='iam_user',
            region='global',
            resource_id=arn,
            resource_name=username,
            raw=raw,
        )
