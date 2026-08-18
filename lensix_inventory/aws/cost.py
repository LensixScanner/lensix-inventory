"""AWS Budgets gathering — one raw record per budget (global, not regional;
Budgets is a single us-east-1-hosted API covering the whole account).

`get_budgets`/`get_notifications` are the two pure fetchers (describe_budgets
+ describe_notifications_for_budget); evaluating "no budgets at all" or "a
budget with no notifications" is left server-side — this module just merges
each budget's notifications into its raw record, the same fused-fetch
pattern as s3.py's per-bucket fan-out.
"""

import boto3


def get_budgets(account_id):
    budgets_client = boto3.client('budgets', region_name='us-east-1')
    result = []
    for page in budgets_client.get_paginator('describe_budgets').paginate(AccountId=account_id):
        result.extend(page.get('Budgets', []))
    return result


def get_notifications(account_id, budget_name):
    budgets_client = boto3.client('budgets', region_name='us-east-1')
    try:
        resp = budgets_client.describe_notifications_for_budget(
            AccountId=account_id,
            BudgetName=budget_name,
        )
        return resp.get('Notifications', [])
    except Exception:
        return []


def gather(writer, account_id):
    for budget in get_budgets(account_id):
        name = budget['BudgetName']
        raw = dict(budget)
        raw['_Notifications'] = get_notifications(account_id, name)
        writer.add_resource(
            resource_type='budget',
            region='global',
            resource_id=name,
            resource_name=name,
            raw=raw,
        )
