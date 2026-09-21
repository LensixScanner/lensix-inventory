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
        vpc_options = domain.get('VPCOptions') or {}
        vpc_id = vpc_options.get('VPCId')
        recorded = writer.add_resource(
            resource_type='elasticsearch_domain',
            region=region,
            resource_id=domain['ARN'],
            resource_name=name,
            scope_id=vpc_id,
            raw=domain,
            tags=get_domain_tags(region, domain['ARN']),
        )
        if not recorded:
            continue
        # VPCOptions is entirely absent for a public (non-VPC) domain --
        # most of them -- so every lookup here defaults to [].
        if vpc_id:
            writer.add_edge(from_type='elasticsearch_domain', from_id=domain['ARN'], to_type='vpc', to_id=vpc_id, relationship='in_vpc')
        for sg_id in vpc_options.get('SecurityGroupIds', []):
            writer.add_edge(from_type='elasticsearch_domain', from_id=domain['ARN'], to_type='security_group', to_id=sg_id, relationship='member_of_sg')
        for subnet_id in vpc_options.get('SubnetIds', []):
            writer.add_edge(from_type='elasticsearch_domain', from_id=domain['ARN'], to_type='subnet', to_id=subnet_id, relationship='in_subnet')
