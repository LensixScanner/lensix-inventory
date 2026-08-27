"""S3 gathering — one merged raw record per bucket.

Unlike most AWS resource types, evaluating an S3 bucket needs several
separate API calls per bucket (there is no single "describe everything
about this bucket" call) — logging config, public-access-block, bucket
policy, lifecycle config, versioning, encryption config are all separate
sub-APIs. For gathering purposes we just call each of those once per bucket
and merge the results into one raw record; how that data gets interpreted
into specific findings only matters for finding evaluation, which happens
server-side.
"""

import json

import boto3
import botocore


def get_buckets():
    s3 = boto3.client('s3', region_name='us-east-1')
    return s3.list_buckets().get('Buckets', [])


def get_bucket_region(s3, bucket_name):
    try:
        resp = s3.get_bucket_location(Bucket=bucket_name)
        return resp.get('LocationConstraint') or 'us-east-1'
    except Exception:
        return 'us-east-1'


def _try(fn, *args, **kwargs):
    """Best-effort sub-API call — most of these throw when the feature isn't
    configured (e.g. NoSuchLifecycleConfiguration), which is itself
    meaningful data (absence == not configured), not an error to surface."""
    try:
        return fn(*args, **kwargs)
    except botocore.exceptions.ClientError as e:
        return {'_error': e.response['Error']['Code']}
    except Exception as e:
        return {'_error': str(e)}


def get_bucket_metadata(s3, bucket_name, account_id):
    """One merged raw record covering every sub-API needed to evaluate this
    bucket."""
    policy_raw = _try(s3.get_bucket_policy, Bucket=bucket_name)
    policy_doc = None
    if isinstance(policy_raw, dict) and 'Policy' in policy_raw:
        try:
            policy_doc = json.loads(policy_raw['Policy'])
        except Exception:
            policy_doc = None

    return {
        'AccountId': account_id,
        'Logging': _try(s3.get_bucket_logging, Bucket=bucket_name),
        'PublicAccessBlock': _try(s3.get_public_access_block, Bucket=bucket_name),
        'Policy': policy_doc,
        # Preserved alongside the parsed Policy doc (which is None both
        # when there's genuinely no policy and when the fetch itself
        # failed for some other reason) so a check can still tell those
        # two cases apart — e.g. "no bucket policy at all" vs "couldn't
        # fetch it" need different findings/no-findings behavior.
        '_PolicyFetchError': policy_raw.get('_error') if isinstance(policy_raw, dict) else None,
        'LifecycleConfiguration': _try(s3.get_bucket_lifecycle_configuration, Bucket=bucket_name),
        'Versioning': _try(s3.get_bucket_versioning, Bucket=bucket_name),
        'Encryption': _try(s3.get_bucket_encryption, Bucket=bucket_name),
    }


def gather(writer, account_id):
    s3 = boto3.client('s3', region_name='us-east-1')
    for bucket in get_buckets():
        name = bucket['Name']
        region = get_bucket_region(s3, name)
        try:
            metadata = get_bucket_metadata(s3, name, account_id)
            raw = {**bucket, **metadata}
            writer.add_resource(
                resource_type='s3_bucket',
                region=region,
                resource_id=name,
                resource_name=name,
                raw=raw,
            )
        except Exception as e:
            writer.add_error(region=region, source=f's3_bucket:{name}', message=e)
