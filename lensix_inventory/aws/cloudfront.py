"""CloudFront gathering — one raw record per distribution (global service,
gathered once, not per-region — same shape as s3.py).

`get_distributions` (list_distributions) and `get_distribution_config`
(get_distribution) are the two pure fetchers, merged per distribution the
same way s3.py merges its per-bucket sub-API calls; access-logging, WAF
attachment, HTTP, TLS-policy, and insecure-origin evaluation is left
server-side.

Whether an origin S3 bucket still exists, and whether it's public, are NOT
re-fetched here — both are correlation against a bucket this tool already
gathers independently in
s3.py (whose `s3_bucket` records already carry bucket existence, via
presence/absence in the uploaded set, and the exact `PublicAccessBlock`
config). Per the "gather each resource type independently" principle (see
README), Lensix can recompute "references a nonexistent/public S3 bucket"
server-side by joining a distribution's origin `DomainName` against the
uploaded `s3_bucket` resources, the same way sg.py skips re-deriving
"is this SG referenced anywhere" from other resources' own data.
"""

import re

import boto3


def get_distributions():
    cf = boto3.client('cloudfront', region_name='us-east-1')
    items = []
    for page in cf.get_paginator('list_distributions').paginate():
        dist_list = page.get('DistributionList', {})
        items.extend(dist_list.get('Items', []))
    return items


def get_distribution_config(dist_id):
    cf = boto3.client('cloudfront', region_name='us-east-1')
    return cf.get_distribution(Id=dist_id)['Distribution']['DistributionConfig']


def get_distribution_tags(arn):
    """CloudFront tags aren't part of list_distributions/get_distribution's
    response — its own separate call, keyed by ARN, with the tag list
    nested an extra level under 'Items' (a CloudFront-specific response
    shape, not the flat {'Tags': [...]} most other services use). Returns
    [] on failure."""
    cf = boto3.client('cloudfront', region_name='us-east-1')
    try:
        return cf.list_tags_for_resource(Resource=arn).get('Tags', {}).get('Items', [])
    except Exception:
        return []


def _extract_bucket_name(domain_name):
    """Extract S3 bucket name from a CloudFront S3 origin DomainName.

    Handles forms like:
      mybucket.s3.amazonaws.com
      mybucket.s3.us-east-1.amazonaws.com
      mybucket.s3-website-us-east-1.amazonaws.com
      mybucket.s3-website.us-east-1.amazonaws.com
    """
    m = re.match(r'^([^.]+)\.s3[.-]', domain_name)
    if m:
        return m.group(1)
    return None


def _is_s3_origin(origin):
    """Return True if this origin targets an S3 bucket (not a custom origin)."""
    return 'S3OriginConfig' in origin and 'CustomOriginConfig' not in origin


def gather(writer):
    for dist in get_distributions():
        dist_id = dist['Id']
        domain_name = dist.get('DomainName', dist_id)
        arn = dist.get('ARN', dist_id)
        raw = dict(dist)
        try:
            raw['_DistributionConfig'] = get_distribution_config(dist_id)
        except Exception as e:
            writer.add_error(region='global', source=f'cloudfront_distribution:{dist_id}', message=e)
        recorded = writer.add_resource(
            resource_type='cloudfront_distribution',
            region='global',
            resource_id=dist_id,
            resource_name=domain_name,
            raw=raw,
            tags=get_distribution_tags(arn),
        )
        if not recorded:
            continue
        # Only the S3-origin case is handled: the bucket name is
        # recoverable from the origin's DomainName string, and s3.py's own
        # resource_id for s3_bucket is exactly that bucket name. A
        # custom/ALB origin's DomainName is just a DNS hostname with no
        # derivable resource_id -- deliberately out of scope here.
        config = raw.get('_DistributionConfig') or {}
        for origin in config.get('Origins', {}).get('Items', []):
            if not _is_s3_origin(origin):
                continue
            bucket = _extract_bucket_name(origin.get('DomainName', ''))
            if bucket:
                writer.add_edge(from_type='cloudfront_distribution', from_id=dist_id, to_type='s3_bucket', to_id=bucket, relationship='routes_to')
