"""API Gateway gathering — REST APIs (v1), their stages, HTTP/WebSocket APIs
(v2), their stages, and custom domain names (v1 + v2).

Only the pure fetchers are included (get_rest_apis, get_rest_stages,
get_http_apis, get_http_stages, get_v1_domain_names, get_v2_domain_names);
access-logging, cache-encryption, tracing, TLS-policy, and public-access
evaluation is left server-side.

Two simplifications relative to a naive per-check fan-out:
  - The attached WAF Web ACL (wafv2.get_web_acl_for_resource) IS raw
    per-resource data (which Web ACL, if any, protects this stage) rather
    than pass/fail logic, so it's merged into each v1 stage's raw record as
    `_WebAcl` (None if none attached) the same fused-fetch pattern as s3.py.
  - A separate client.get_rest_api call per API is redundant with what
    get_rest_apis already returns (GetRestApis items already include
    `endpointConfiguration`), so it isn't re-fetched.
  - Certificate expiration/validation status is NOT re-fetched here — the
    certificate ARN (`regionalCertificateArn`/`certificateArn` for v1,
    `DomainNameConfigurations[].CertificateArn` for v2), and the
    `acm_certificate` resources gathered by acm.py already carry the full
    NotAfter/Status data for that same ARN. Per the "gather each resource
    type independently" principle (see README), Lensix can join these
    server-side rather than this tool re-fetching the same certificate.
"""

import boto3
from botocore.exceptions import ClientError


def get_rest_apis(region):
    client = boto3.client('apigateway', region_name=region)
    apis = []
    for page in client.get_paginator('get_rest_apis').paginate():
        apis.extend(page.get('items', []))
    return apis


def get_rest_stages(region, rest_api_id):
    client = boto3.client('apigateway', region_name=region)
    return client.get_stages(restApiId=rest_api_id).get('item', [])


def get_http_apis(region):
    client = boto3.client('apigatewayv2', region_name=region)
    apis = []
    for page in client.get_paginator('get_apis').paginate():
        apis.extend(page.get('Items', []))
    return apis


def get_http_stages(region, api_id):
    client = boto3.client('apigatewayv2', region_name=region)
    stages = []
    for page in client.get_paginator('get_stages').paginate(ApiId=api_id):
        stages.extend(page.get('Items', []))
    return stages


def get_v1_domain_names(region):
    client = boto3.client('apigateway', region_name=region)
    domains = []
    for page in client.get_paginator('get_domain_names').paginate():
        domains.extend(page.get('items', []))
    return domains


def get_v2_domain_names(region):
    client = boto3.client('apigatewayv2', region_name=region)
    domains = []
    for page in client.get_paginator('get_domain_names').paginate():
        domains.extend(page.get('Items', []))
    return domains


def get_stage_web_acl(region, api_id, stage_name):
    """Raw data (which Web ACL, if any, protects this stage), not a
    computed pass/fail decision."""
    wafv2 = boto3.client('wafv2', region_name=region)
    stage_arn = f'arn:aws:apigateway:{region}::/restapis/{api_id}/stages/{stage_name}'
    try:
        return wafv2.get_web_acl_for_resource(ResourceArn=stage_arn).get('WebACL')
    except ClientError as e:
        if e.response.get('Error', {}).get('Code', '') == 'WAFNonexistentItemException':
            return None
        raise


def gather(region, writer):
    rest_apis = get_rest_apis(region)
    for api in rest_apis:
        writer.add_resource(
            resource_type='apigw_rest_api', region=region, resource_id=api['id'],
            resource_name=api.get('name', api['id']), raw=api,
        )

    for api in rest_apis:
        api_id = api['id']
        api_name = api.get('name', api_id)
        try:
            stages = get_rest_stages(region, api_id)
        except Exception as e:
            writer.add_error(region=region, source=f'apigw_stage:{api_id}', message=e)
            continue
        for stage in stages:
            stage_name = stage['stageName']
            raw = dict(stage)
            raw['_ApiId'] = api_id
            raw['_ApiName'] = api_name
            raw['_ApiType'] = 'REST'
            try:
                raw['_WebAcl'] = get_stage_web_acl(region, api_id, stage_name)
            except Exception as e:
                writer.add_error(region=region, source=f'apigw_stage:{api_id}/{stage_name}', message=e)
            writer.add_resource(
                resource_type='apigw_stage', region=region, resource_id=f'{api_id}/{stage_name}',
                resource_name=f'{api_name}/{stage_name}', raw=raw,
            )

    http_apis = get_http_apis(region)
    for api in http_apis:
        writer.add_resource(
            resource_type='apigw_http_api', region=region, resource_id=api['ApiId'],
            resource_name=api.get('Name', api['ApiId']), raw=api,
        )

    for api in http_apis:
        api_id = api['ApiId']
        api_name = api.get('Name', api_id)
        try:
            stages = get_http_stages(region, api_id)
        except Exception as e:
            writer.add_error(region=region, source=f'apigw_stage:{api_id}', message=e)
            continue
        for stage in stages:
            stage_name = stage['StageName']
            raw = dict(stage)
            raw['_ApiId'] = api_id
            raw['_ApiName'] = api_name
            raw['_ApiType'] = 'HTTP'
            writer.add_resource(
                resource_type='apigw_stage', region=region, resource_id=f'{api_id}/{stage_name}',
                resource_name=f'{api_name}/{stage_name}', raw=raw,
            )

    for domain in get_v1_domain_names(region):
        name = domain['domainName']
        raw = dict(domain)
        raw['_ApiType'] = 'REST'
        writer.add_resource(
            resource_type='apigw_domain', region=region, resource_id=name,
            resource_name=name, raw=raw,
        )

    for domain in get_v2_domain_names(region):
        name = domain['DomainName']
        raw = dict(domain)
        raw['_ApiType'] = 'HTTP'
        writer.add_resource(
            resource_type='apigw_domain', region=region, resource_id=name,
            resource_name=name, raw=raw,
        )
