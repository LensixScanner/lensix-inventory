"""AWS Savings Plans gathering — one raw record per Savings Plan the
account has actually purchased (its holdings, not the published rate
catalog — lensix-cost-light's own aws/savingsplans.py owns that side,
ingesting AWS's public Savings Plan price list from the Price List Bulk
API separately). Feeds lensix-cost-light's commitments input, alongside
reserved_instances.py's RI holdings, so it can account for what's already
covered instead of pricing every resource at on-demand rates.

Global like cost.py's Budgets: a Savings Plan is an account-wide
commitment, not scoped to one region, and describe_savings_plans takes no
region-specific filter — any region's endpoint returns the whole
account's plans, so this is called once, not per region (confirmed
against the installed botocore service model: describe_savings_plans has
no AccountId parameter either, unlike Budgets — no account_id argument
needed here).

describe_savings_plans has no registered botocore paginator (confirmed:
`client.can_paginate('describe_savings_plans')` is False) even though its
output carries `nextToken` and its input accepts one back — paginated by
hand, same style as mq.py's get_brokers. Unlike Budgets, no separate tags
lookup is needed either: each plan record already carries its own `tags`
as a flat {key: value} dict (confirmed against the botocore model), which
is exactly the shape add_resource()'s own tags= parameter already
supports for Azure/GCP-style flat tag dicts.
"""

import boto3


def get_savings_plans():
    client = boto3.client('savingsplans', region_name='us-east-1')
    result = []
    kwargs = {}
    while True:
        resp = client.describe_savings_plans(**kwargs)
        result.extend(resp.get('savingsPlans', []))
        next_token = resp.get('nextToken')
        if not next_token:
            break
        kwargs['nextToken'] = next_token
    return result


def gather(writer):
    for plan in get_savings_plans():
        plan_id = plan.get('savingsPlanId', '')
        writer.add_resource(
            resource_type='savings_plan',
            region='global',
            resource_id=plan.get('savingsPlanArn') or plan_id,
            resource_name=plan_id,
            raw=dict(plan),
            tags=plan.get('tags'),
        )
