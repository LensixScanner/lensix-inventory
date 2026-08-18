"""IAM gathering — users.

IAM is global, so this module never loops regions, mirroring s3.py's
`gather(writer)` pattern instead of `gather(region, writer)`.

Access key age, last console login, MFA status, and similar per-user
security signals are all derived from `get_credential_report()` — a
stateful generate-then-poll workflow (generate_credential_report, then poll
get_credential_report until ready) rather than a simple list/describe call,
and is explicitly out of scope for this tool (see README.md) — a similarly
awkward fit for a point-in-time snapshot as any generate-then-poll pattern.

The same `iam_user` resource shape is trivially reproducible from
list_users() instead — a plain list call, no generate/poll needed — so
that's what's used here. This is also the sole owner of the `iam_user`
resource type in this tool (account.py covers every other IAM/account-
security resource type, but deliberately not this one, to avoid gathering
the same users twice) — this module folds in both list_attached_user_policies
and list_groups_for_user (needed for direct-policy and group-membership
evaluation) per user as `_AttachedPolicies`/`_Groups` in each user's raw
record, matching s3.py's fused fan-out pattern.
"""

import boto3


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


def gather(writer):
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

        writer.add_resource(
            resource_type='iam_user',
            region='global',
            resource_id=arn,
            resource_name=username,
            raw=raw,
        )
