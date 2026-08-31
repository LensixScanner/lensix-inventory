"""ACM (Certificate Manager) gathering — one raw record per certificate.

Two pure fetchers: `list_certificates` (all statuses) and
`describe_certificate` for the full detail. Expiration and validation-status
evaluation is intentionally not included — Lensix recomputes those
server-side from the full `Certificate` dict (Status, NotAfter,
DomainValidationOptions, ...) uploaded here.

Tags need their own list_tags_for_certificate call (not in
describe_certificate's own response) — passed through for tag-based
suppression.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


def get_certificate_arns(region):
    acm = boto3.client('acm', region_name=region, config=_BOTO_CFG)
    arns = []
    for page in acm.get_paginator('list_certificates').paginate():
        for cert in page.get('CertificateSummaryList', []):
            arns.append(cert['CertificateArn'])
    return arns


def get_certificate(region, arn):
    acm = boto3.client('acm', region_name=region, config=_BOTO_CFG)
    return acm.describe_certificate(CertificateArn=arn).get('Certificate', {})


def get_certificate_tags(region, arn):
    """describe_certificate doesn't include tags — ACM's own (unpaginated)
    list_tags_for_certificate call. Returns [] on failure."""
    acm = boto3.client('acm', region_name=region, config=_BOTO_CFG)
    try:
        return acm.list_tags_for_certificate(CertificateArn=arn).get('Tags', [])
    except Exception:
        return []


def gather(region, writer):
    for arn in get_certificate_arns(region):
        try:
            cert = get_certificate(region, arn)
        except Exception as e:
            writer.add_error(region=region, source=f'acm_certificate:{arn}', message=e)
            continue
        domain = cert.get('DomainName', arn)
        writer.add_resource(
            resource_type='acm_certificate',
            region=region,
            resource_id=arn,
            resource_name=domain,
            raw=cert,
            tags=get_certificate_tags(region, arn),
        )
