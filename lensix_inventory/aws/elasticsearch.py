"""Elasticsearch/OpenSearch (legacy `es` API) gathering — one raw record
per domain.

`get_domains` (list_domain_names) and `get_domain_detail`
(describe_elasticsearch_domain) already return everything needed for
encryption, logging, TLS, HTTP, TLS-policy, public-access, and VPC
evaluation (EncryptionAtRestOptions, LogPublishingOptions,
NodeToNodeEncryptionOptions, DomainEndpointOptions, AccessPolicies,
VPCOptions) in one call — no extra fan-out needed. That evaluation itself
is left server-side.
"""

import boto3


def get_domains(region):
    es = boto3.client('es', region_name=region)
    return [d['DomainName'] for d in es.list_domain_names()['DomainNames']]


def get_domain_detail(region, name):
    es = boto3.client('es', region_name=region)
    return es.describe_elasticsearch_domain(DomainName=name)['DomainStatus']


def get_domain_tags(region, arn):
    """describe_elasticsearch_domain doesn't include tags — its own
    separate, unpaginated list_tags call, keyed by ARN. Returns [] on
    failure."""
    es = boto3.client('es', region_name=region)
    try:
        return es.list_tags(ARN=arn).get('TagList', [])
    except Exception:
        return []


def gather(region, writer):
    for name in get_domains(region):
        try:
            domain = get_domain_detail(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'elasticsearch_domain:{name}', message=e)
            continue
        writer.add_resource(
            resource_type='elasticsearch_domain',
            region=region,
            resource_id=domain['ARN'],
            resource_name=name,
            scope_id=domain.get('VPCOptions', {}).get('VPCId') if domain.get('VPCOptions') else None,
            raw=domain,
            tags=get_domain_tags(region, domain['ARN']),
        )
