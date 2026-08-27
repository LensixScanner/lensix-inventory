"""Unit tests for lensix_inventory.common.output — the InventoryWriter and
the shared permission-error detector every provider's summary printing
relies on."""

import gzip
import json
from datetime import datetime, timezone

import pytest

from lensix_inventory.common.output import InventoryWriter, is_permission_error


def _writer():
    return InventoryWriter(provider='aws', account_id='123456789012', tool_version='0.1.0')


class TestAddResource:
    def test_records_a_minimal_resource(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='web-1', raw={'InstanceId': 'i-1'})
        assert w.records == [{
            'kind': 'resource', 'resource_type': 'ec2_instance', 'region': 'us-east-1',
            'resource_id': 'i-1', 'resource_name': 'web-1', 'raw': {'InstanceId': 'i-1'},
        }]

    def test_scope_id_included_only_when_given(self):
        w = _writer()
        w.add_resource(resource_type='subnet', region='us-east-1', resource_id='subnet-1',
                        resource_name='subnet-1', raw={}, scope_id='vpc-1')
        assert w.records[0]['scope_id'] == 'vpc-1'

    def test_scope_id_omitted_when_none(self):
        w = _writer()
        w.add_resource(resource_type='vpc', region='us-east-1', resource_id='vpc-1',
                        resource_name='vpc-1', raw={})
        assert 'scope_id' not in w.records[0]

    def test_secret_scan_hits_included_only_when_given(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={}, secret_scan_hits=['AWS Secret Access Key'])
        assert w.records[0]['secret_scan_hits'] == ['AWS Secret Access Key']

    def test_secret_scan_hits_omitted_when_none(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={})
        assert 'secret_scan_hits' not in w.records[0]

    def test_empty_list_secret_scan_hits_is_still_included(self):
        # Distinguishes "scanned, found nothing" (empty list, still present)
        # from "never scanned" (key absent entirely).
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={}, secret_scan_hits=[])
        assert w.records[0]['secret_scan_hits'] == []

    def test_resource_counts_tally_by_type(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1', resource_name='i-1', raw={})
        w.add_resource(resource_type='ec2_instance', region='us-west-2', resource_id='i-2', resource_name='i-2', raw={})
        w.add_resource(resource_type='s3_bucket', region='global', resource_id='b-1', resource_name='b-1', raw={})
        assert w.resource_counts == {'ec2_instance': 2, 's3_bucket': 1}

    def test_records_accumulate_in_call_order(self):
        w = _writer()
        w.add_resource(resource_type='a', region='r', resource_id='1', resource_name='1', raw={})
        w.add_resource(resource_type='b', region='r', resource_id='2', resource_name='2', raw={})
        assert [r['resource_type'] for r in w.records] == ['a', 'b']

    def test_records_property_returns_a_copy(self):
        # Mutating the returned list must not affect the writer's own state.
        w = _writer()
        w.add_resource(resource_type='a', region='r', resource_id='1', resource_name='1', raw={})
        snapshot = w.records
        snapshot.append({'fake': True})
        assert len(w.records) == 1

    def test_falsy_region_is_not_added_to_regions_but_is_still_recorded(self):
        w = _writer()
        w.add_resource(resource_type='a', region='', resource_id='1', resource_name='1', raw={})
        assert w.records[0]['region'] == ''
        assert w._regions == set()  # empty region string must not pollute the manifest's region list


class TestAddError:
    def test_records_an_error(self):
        w = _writer()
        w.add_error(region='us-east-1', source='ec2 (instances)', message=RuntimeError('boom'))
        assert w.errors == [{'region': 'us-east-1', 'source': 'ec2 (instances)', 'message': 'boom'}]

    def test_message_is_stringified_even_if_passed_as_an_exception(self):
        w = _writer()
        w.add_error(region='global', source='s3', message=ValueError('bad bucket'))
        assert w.errors[0]['message'] == 'bad bucket'

    def test_errors_property_returns_a_copy(self):
        w = _writer()
        w.add_error(region='global', source='x', message='boom')
        snapshot = w.errors
        snapshot.append({'fake': True})
        assert len(w.errors) == 1

    def test_multiple_errors_accumulate_in_order(self):
        w = _writer()
        w.add_error(region='us-east-1', source='a', message='first')
        w.add_error(region='us-west-2', source='b', message='second')
        assert [e['source'] for e in w.errors] == ['a', 'b']


class TestIsPermissionError:
    @pytest.mark.parametrize('message', [
        'An error occurred (AccessDenied) when calling the ListBuckets operation',
        'UnauthorizedOperation: You are not authorized to perform this operation',
        'AuthorizationFailed: The client does not have authorization',
        'AuthorizationError',
        'HttpResponseError: Forbidden',
        'PERMISSION_DENIED: Permission denied on resource',
        'the caller does not have permission and is not authorized to access',
        'does not have authorization to perform this action',
        'InsufficientAccountPermissions',
        'request failed with status 403',
    ])
    def test_recognizes_permission_errors_across_providers(self, message):
        assert is_permission_error(message) is True

    @pytest.mark.parametrize('message', [
        'Connection timed out',
        'ResourceNotFoundException: no such bucket',
        'ValidationException: invalid parameter',
        '',
    ])
    def test_does_not_flag_unrelated_errors(self, message):
        assert is_permission_error(message) is False

    def test_case_insensitive(self):
        assert is_permission_error('accessdenied') is True
        assert is_permission_error('ACCESSDENIED') is True

    def test_accepts_non_string_input(self):
        assert is_permission_error(RuntimeError('AccessDenied')) is True


class TestWrite:
    def _read_lines(self, path):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_writes_manifest_then_records_then_errors(self, tmp_path):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='web-1', raw={'InstanceId': 'i-1'})
        w.add_error(region='us-west-2', source='ec2 (instances)', message='boom')
        path = tmp_path / 'out.ndjson.gz'
        manifest = w.write(str(path))

        lines = self._read_lines(str(path))
        assert lines[0]['kind'] == 'manifest'
        assert lines[1]['kind'] == 'resource'
        assert lines[1]['resource_id'] == 'i-1'
        assert lines[2]['kind'] == 'error'
        assert lines[2]['source'] == 'ec2 (instances)'
        assert manifest == lines[0]

    def test_manifest_fields(self, tmp_path):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1', resource_name='i-1', raw={})
        w.add_resource(resource_type='s3_bucket', region='us-west-2', resource_id='b-1', resource_name='b-1', raw={})
        w.add_error(region='global', source='x', message='boom')
        path = tmp_path / 'out.ndjson.gz'
        manifest = w.write(str(path))

        assert manifest['format_version'] == '1.0'
        assert manifest['tool_version'] == '0.1.0'
        assert manifest['provider'] == 'aws'
        assert manifest['account_id'] == '123456789012'
        assert manifest['regions'] == ['us-east-1', 'us-west-2']
        assert manifest['resource_counts'] == {'ec2_instance': 1, 's3_bucket': 1}
        assert manifest['total_resources'] == 2
        assert manifest['error_count'] == 1
        # generated_at must be a real, parseable ISO-8601 timestamp.
        datetime.fromisoformat(manifest['generated_at'])

    def test_empty_writer_still_writes_a_valid_manifest(self, tmp_path):
        w = _writer()
        path = tmp_path / 'out.ndjson.gz'
        manifest = w.write(str(path))
        assert manifest['total_resources'] == 0
        assert manifest['regions'] == []
        assert self._read_lines(str(path)) == [manifest]

    def test_datetime_values_in_raw_are_serialized_as_isoformat(self, tmp_path):
        w = _writer()
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'LaunchTime': ts})
        path = tmp_path / 'out.ndjson.gz'
        w.write(str(path))
        lines = self._read_lines(str(path))
        assert lines[1]['raw']['LaunchTime'] == ts.isoformat()

    def test_non_json_native_values_fall_back_to_str(self, tmp_path):
        class Weird:
            def __str__(self):
                return 'weird-value'
        w = _writer()
        w.add_resource(resource_type='x', region='global', resource_id='1', resource_name='1',
                        raw={'thing': Weird()})
        path = tmp_path / 'out.ndjson.gz'
        w.write(str(path))
        lines = self._read_lines(str(path))
        assert lines[1]['raw']['thing'] == 'weird-value'

    def test_file_permissions_restricted_to_owner(self, tmp_path):
        w = _writer()
        path = tmp_path / 'out.ndjson.gz'
        w.write(str(path))
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_returns_the_same_manifest_dict_that_was_written(self, tmp_path):
        w = _writer()
        w.add_resource(resource_type='x', region='global', resource_id='1', resource_name='1', raw={})
        path = tmp_path / 'out.ndjson.gz'
        manifest = w.write(str(path))
        assert self._read_lines(str(path))[0] == manifest
