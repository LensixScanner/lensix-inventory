"""Account/region discovery. This tool runs standalone on the customer's own
machine against their own credentials (whatever their local AWS CLI/SDK
credential chain already resolves to: profile, env vars, instance role,
SSO, ...), so there's no database, no multi-tenant bookkeeping — just the
account ID and the list of regions to scan.
"""

import boto3


def get_account_id():
    return boto3.client('sts').get_caller_identity()['Account']


def get_regions():
    ec2 = boto3.client('ec2', region_name='us-east-1')
    resp = ec2.describe_regions(
        Filters=[{'Name': 'opt-in-status', 'Values': ['opt-in-not-required', 'opted-in']}]
    )
    return [r['RegionName'] for r in resp['Regions']]
