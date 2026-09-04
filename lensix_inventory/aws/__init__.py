"""AWS provider orchestrator — runs every gather module across every region
(plus the account-wide/global modules once) and returns a populated
InventoryWriter.

Every module under lensix_inventory/aws/ (see each one's own docstring for
what it gathers and why) is registered below in one of three buckets,
matching each module's
`gather` signature:
  - REGIONAL_MODULES   — gather(region, writer), called once per region.
  - GLOBAL_MODULES      — gather(writer), called once total.
  - GLOBAL_MODULES_WITH_ACCOUNT — gather(writer, account_id), called once
    total (needs the account ID for cross-account-principal evaluation,
    e.g. s3.py's bucket-policy checks).

account.py is the one module split across two of these: its regional
gather() (KMS/CloudTrail/Config/GuardDuty/Access-Analyzer/CloudWatch/X-Ray)
lives in REGIONAL_MODULES, and its separately-named gather_global()
(IAM roles/groups/policies/certs/MFA devices/password policy/account
summary/SSO) is called explicitly alongside the other global modules.

Each region/module failure is caught and recorded as an error rather than
aborting the whole run.
"""

from .. import __version__
from ..common.output import InventoryWriter
from . import (
    account, acm, apigateway, athena, autoscaling, cicd, cloudfront, cost,
    dnsinventory, documentdb, dynamodb, ebs, ec2, ecr, ecs, efs, eks,
    elasticache, elasticsearch, emr, glue, kinesis, lambda_, lb, mq, msk,
    neptune, network, rds, redshift, reserved_instances, route53, s3,
    sagemaker, savingsplans, secretsmanager, sg, sns, sqs, ssm, transfer,
    user, vpc, workspaces,
)
from .session import get_account_id, get_regions

REGIONAL_MODULES = [
    ('account', account.gather),
    ('acm', acm.gather),
    ('apigateway', apigateway.gather),
    ('athena', athena.gather),
    ('autoscaling', autoscaling.gather),
    ('cicd', cicd.gather),
    ('documentdb', documentdb.gather),
    ('dynamodb', dynamodb.gather),
    ('ebs', ebs.gather),
    ('ec2', ec2.gather),
    ('ecr', ecr.gather),
    ('ecs', ecs.gather),
    ('efs', efs.gather),
    ('eks', eks.gather),
    ('elasticache', elasticache.gather),
    ('elasticsearch', elasticsearch.gather),
    ('emr', emr.gather),
    ('glue', glue.gather),
    ('kinesis', kinesis.gather),
    ('lambda', lambda_.gather),
    ('lb', lb.gather),
    ('mq', mq.gather),
    ('msk', msk.gather),
    ('neptune', neptune.gather),
    ('network', network.gather),
    ('rds', rds.gather),
    ('redshift', redshift.gather),
    ('reserved_instances', reserved_instances.gather),
    ('sagemaker', sagemaker.gather),
    ('secretsmanager', secretsmanager.gather),
    ('sg', sg.gather),
    ('sns', sns.gather),
    ('sqs', sqs.gather),
    ('ssm', ssm.gather),
    ('transfer', transfer.gather),
    ('vpc', vpc.gather),
    ('workspaces', workspaces.gather),
]

GLOBAL_MODULES = [
    ('cloudfront', cloudfront.gather),
    ('dnsinventory', dnsinventory.gather),
    ('route53', route53.gather),
    ('savingsplans', savingsplans.gather),
    ('user', user.gather),
]

GLOBAL_MODULES_WITH_ACCOUNT = [
    ('account (global)', account.gather_global),
    ('cost', cost.gather),
    ('s3', s3.gather),
]


def run(regions=None):
    account_id = get_account_id()
    writer = InventoryWriter(provider='aws', account_id=account_id, tool_version=__version__)

    target_regions = regions or get_regions()

    for region in target_regions:
        print(f"[aws] region {region}")
        for name, gather_fn in REGIONAL_MODULES:
            try:
                print(f"  [aws] {name} ...", end=' ', flush=True)
                gather_fn(region, writer)
                print("done")
            except Exception as e:
                print(f"error: {e}")
                writer.add_error(region=region, source=name, message=e)

    for name, gather_fn in GLOBAL_MODULES:
        print(f"[aws] {name} (global) ...", end=' ', flush=True)
        try:
            gather_fn(writer)
            print("done")
        except Exception as e:
            print(f"error: {e}")
            writer.add_error(region='global', source=name, message=e)

    for name, gather_fn in GLOBAL_MODULES_WITH_ACCOUNT:
        print(f"[aws] {name} (global) ...", end=' ', flush=True)
        try:
            gather_fn(writer, account_id)
            print("done")
        except Exception as e:
            print(f"error: {e}")
            writer.add_error(region='global', source=name, message=e)

    return writer
