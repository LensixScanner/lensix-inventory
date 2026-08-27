"""Unit tests for lensix_inventory.aws.s3 — S3 buckets (fused per-bucket sub-API record)."""

import json
from unittest.mock import MagicMock, patch

import botocore.exceptions

import lensix_inventory.aws.s3 as m


def _client_error(code):
    return botocore.exceptions.ClientError({'Error': {'Code': code}}, 'GetBucketX')


def _s3_client(buckets=None, region_by_bucket=None, sub_api_overrides=None):
    """sub_api_overrides: {bucket_name: {'get_bucket_logging': <return or Exception>, ...}}"""
    client = MagicMock()
    client.list_buckets.return_value = {'Buckets': buckets or []}
    region_by_bucket = region_by_bucket or {}
    sub_api_overrides = sub_api_overrides or {}

    def _region(Bucket):
        return {'LocationConstraint': region_by_bucket.get(Bucket)}
    client.get_bucket_location.side_effect = _region

    sub_apis = ['get_bucket_logging', 'get_public_access_block', 'get_bucket_policy',
                'get_bucket_lifecycle_configuration', 'get_bucket_versioning', 'get_bucket_encryption']
    for api in sub_apis:
        def _make(api_name):
            def _call(Bucket):
                override = sub_api_overrides.get(Bucket, {}).get(api_name)
                if isinstance(override, Exception):
                    raise override
                return override if override is not None else {}
            return _call
        getattr(client, api).side_effect = _make(api)
    return client


class TestGetBucketRegion:
    def test_returns_the_location_constraint(self):
        client = _s3_client(region_by_bucket={'my-bucket': 'eu-west-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_bucket_region(client, 'my-bucket') == 'eu-west-1'

    def test_null_location_constraint_means_us_east_1(self):
        client = _s3_client(region_by_bucket={'my-bucket': None})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_bucket_region(client, 'my-bucket') == 'us-east-1'

    def test_a_failure_defaults_to_us_east_1(self):
        client = MagicMock()
        client.get_bucket_location.side_effect = RuntimeError('boom')
        assert m.get_bucket_region(client, 'my-bucket') == 'us-east-1'


class TestTry:
    def test_returns_the_call_result_on_success(self):
        assert m._try(lambda: {'ok': True}) == {'ok': True}

    def test_a_client_error_returns_the_error_code_not_a_raised_exception(self):
        def _raise():
            raise _client_error('NoSuchLifecycleConfiguration')
        assert m._try(_raise) == {'_error': 'NoSuchLifecycleConfiguration'}

    def test_a_generic_exception_is_also_captured_not_raised(self):
        def _raise():
            raise RuntimeError('boom')
        assert m._try(_raise) == {'_error': 'boom'}


class TestGetBucketMetadata:
    def test_merges_every_sub_api_into_one_dict(self):
        client = _s3_client(sub_api_overrides={'my-bucket': {
            'get_bucket_versioning': {'Status': 'Enabled'},
            'get_bucket_encryption': {'ServerSideEncryptionConfiguration': {}},
        }})
        meta = m.get_bucket_metadata(client, 'my-bucket', '123456789012')
        assert meta['AccountId'] == '123456789012'
        assert meta['Versioning'] == {'Status': 'Enabled'}
        assert meta['Encryption'] == {'ServerSideEncryptionConfiguration': {}}

    def test_parses_the_bucket_policy_json_when_present(self):
        policy_doc = {'Statement': [{'Effect': 'Allow'}]}
        client = _s3_client(sub_api_overrides={'my-bucket': {
            'get_bucket_policy': {'Policy': json.dumps(policy_doc)},
        }})
        meta = m.get_bucket_metadata(client, 'my-bucket', '123456789012')
        assert meta['Policy'] == policy_doc
        assert meta['_PolicyFetchError'] is None

    def test_no_policy_configured_leaves_policy_none_and_preserves_the_error_code(self):
        client = _s3_client(sub_api_overrides={'my-bucket': {
            'get_bucket_policy': _client_error('NoSuchBucketPolicy'),
        }})
        meta = m.get_bucket_metadata(client, 'my-bucket', '123456789012')
        assert meta['Policy'] is None
        assert meta['_PolicyFetchError'] == 'NoSuchBucketPolicy'

    def test_an_unrelated_policy_fetch_error_is_also_preserved(self):
        client = _s3_client(sub_api_overrides={'my-bucket': {
            'get_bucket_policy': _client_error('AccessDenied'),
        }})
        meta = m.get_bucket_metadata(client, 'my-bucket', '123456789012')
        assert meta['Policy'] is None
        assert meta['_PolicyFetchError'] == 'AccessDenied'

    def test_a_malformed_policy_document_leaves_policy_none_rather_than_raising(self):
        client = _s3_client(sub_api_overrides={'my-bucket': {
            'get_bucket_policy': {'Policy': 'not-json'},
        }})
        meta = m.get_bucket_metadata(client, 'my-bucket', '123456789012')
        assert meta['Policy'] is None
        assert meta['_PolicyFetchError'] is None


class TestGather:
    def test_adds_one_resource_per_bucket_with_metadata_merged_in(self):
        w = MagicMock()
        bucket = {'Name': 'my-bucket', 'CreationDate': 'x'}
        client = _s3_client(buckets=[bucket], region_by_bucket={'my-bucket': 'eu-west-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 's3_bucket'
        assert kwargs['region'] == 'eu-west-1'
        assert kwargs['resource_id'] == 'my-bucket'
        assert kwargs['raw']['Name'] == 'my-bucket'
        assert kwargs['raw']['AccountId'] == '123456789012'

    def test_sub_api_failures_are_swallowed_internally_not_surfaced_as_bucket_errors(self):
        # Every sub-API call goes through _try(), which never re-raises —
        # so a failing sub-API (e.g. no bucket logging configured) still
        # leaves the bucket fully gathered, not recorded as an error.
        w = MagicMock()
        bucket = {'Name': 'my-bucket'}
        client = _s3_client(buckets=[bucket], region_by_bucket={'my-bucket': 'us-east-1'})
        client.get_bucket_logging.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_error.assert_not_called()
        w.add_resource.assert_called_once()

    def test_a_writer_level_failure_for_one_bucket_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'Name': 'good-bucket'}
        bad = {'Name': 'bad-bucket'}
        w.add_resource.side_effect = [RuntimeError('boom'), None]
        client = _s3_client(buckets=[bad, good], region_by_bucket={'good-bucket': 'us-east-1', 'bad-bucket': 'us-east-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 's3_bucket:bad-bucket'
        assert w.add_resource.call_count == 2

    def test_no_buckets_gathers_nothing(self):
        w = MagicMock()
        client = _s3_client(buckets=[])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_resource.assert_not_called()
