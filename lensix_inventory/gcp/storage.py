"""Cloud Storage gathering — one merged raw record per bucket.

Like aws/s3.py, the bucket's own list/get representation already carries
most of what evaluation needs (iamConfiguration, logging, versioning,
encryption, retentionPolicy, lifecycle) — the one sub-API fan-out call is
getIamPolicy (needed for public-access evaluation), which is merged into
the bucket's raw record the same way aws/s3.py merges get_bucket_policy /
get_public_access_block / etc. into one record per bucket. Public access,
uniform bucket-level access, logging, versioning, CMEK, retention policy/
lock, and lifecycle-rule evaluation is left server-side.
"""

from googleapiclient import discovery


def get_buckets(storage, project_id):
    buckets = []
    request = storage.buckets().list(project=project_id)
    while request is not None:
        resp = request.execute()
        buckets.extend(resp.get('items', []))
        request = storage.buckets().list_next(previous_request=request, previous_response=resp)
    return buckets


def get_bucket_iam_policy(storage, bucket_name):
    resp = storage.buckets().getIamPolicy(bucket=bucket_name).execute()
    return resp.get('bindings', [])


def gather(project_id, credentials, writer):
    storage = discovery.build('storage', 'v1', credentials=credentials)

    try:
        buckets = get_buckets(storage, project_id)
    except Exception as e:
        writer.add_error(region='global', source='storage_bucket', message=e)
        return

    for bucket in buckets:
        name = bucket.get('name', '')
        region = (bucket.get('location') or 'global').lower()

        raw = dict(bucket)
        try:
            raw['_IamPolicyBindings'] = get_bucket_iam_policy(storage, name)
        except Exception as e:
            writer.add_error(region=region, source=f'storage_bucket:{name}', message=e)

        writer.add_resource(
            resource_type='storage_bucket',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=raw,
            tags=raw.get('labels'),
        )
