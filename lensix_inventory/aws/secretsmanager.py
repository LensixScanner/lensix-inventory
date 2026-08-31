"""Secrets Manager gathering — secrets.

list_secrets already returns everything evaluation needs (RotationEnabled,
KmsKeyId, RotationRules) in one shot — a clean fetch/evaluate split; the
fetch result becomes the raw `secretsmanager_secret` record as-is.

Secret *values* are never fetched — only metadata — so there's nothing here
that needs the secret-redaction treatment used elsewhere in this tool (see
common/secrets.py); this module never touches a secret's actual value.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


def get_secrets(region):
    sm = boto3.client('secretsmanager', region_name=region, config=_BOTO_CFG)
    secrets = []
    kwargs = {}
    while True:
        resp = sm.list_secrets(**kwargs)
        secrets.extend(resp.get('SecretList', []))
        next_token = resp.get('NextToken')
        if not next_token:
            break
        kwargs['NextToken'] = next_token
    return secrets


def gather(region, writer):
    for secret in get_secrets(region):
        writer.add_resource(
            resource_type='secretsmanager_secret',
            region=region,
            resource_id=secret['ARN'],
            resource_name=secret['Name'],
            raw=secret,
            # list_secrets already includes each secret's own Tags inline.
            tags=secret.get('Tags'),
        )
