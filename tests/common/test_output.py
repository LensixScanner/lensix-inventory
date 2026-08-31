"""Unit tests for lensix_inventory.common.output — the InventoryWriter and
the shared permission-error detector every provider's summary printing
relies on."""

import gzip
import json
from datetime import datetime, timezone

import pytest

from lensix_inventory.common.output import InventoryWriter, is_permission_error, _normalize_tags, parse_tag_suppression


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


class TestNormalizeTags:
    def test_aws_list_of_dicts(self):
        assert _normalize_tags([{'Key': 'Name', 'Value': 'web-1'}, {'Key': 'Env', 'Value': 'prod'}]) == {
            'Name': 'web-1', 'Env': 'prod',
        }

    def test_azure_gcp_flat_dict_passes_through(self):
        assert _normalize_tags({'Name': 'web-1'}) == {'Name': 'web-1'}

    def test_none_returns_empty(self):
        assert _normalize_tags(None) == {}

    def test_empty_list_returns_empty(self):
        assert _normalize_tags([]) == {}

    def test_aws_entries_missing_a_key_are_skipped(self):
        assert _normalize_tags([{'Value': 'orphan'}, {'Key': 'Env', 'Value': 'prod'}]) == {'Env': 'prod'}

    def test_lowercase_key_value_shape_used_by_ecs_eks_glue_msk_and_others(self):
        assert _normalize_tags([{'key': 'Name', 'value': 'web-1'}, {'key': 'Env', 'value': 'prod'}]) == {
            'Name': 'web-1', 'Env': 'prod',
        }

    def test_lowercase_entries_missing_a_key_are_skipped(self):
        assert _normalize_tags([{'value': 'orphan'}, {'key': 'Env', 'value': 'prod'}]) == {'Env': 'prod'}

    def test_mixed_case_lists_are_not_expected_but_do_not_crash(self):
        # Real AWS responses are consistently one case or the other per
        # service — this just confirms a per-entry lookup, not a
        # whole-list case sniff, so nothing stranger than "half the tags
        # get skipped" could happen even on an unrealistic mixed input.
        assert _normalize_tags([{'Key': 'A', 'Value': '1'}, {'key': 'B', 'value': '2'}]) == {'A': '1', 'B': '2'}

    def test_tagkey_tagvalue_shape_used_by_kms(self):
        assert _normalize_tags([{'TagKey': 'Name', 'TagValue': 'web-1'}, {'TagKey': 'Env', 'TagValue': 'prod'}]) == {
            'Name': 'web-1', 'Env': 'prod',
        }

    def test_tagkey_entries_missing_a_key_are_skipped(self):
        assert _normalize_tags([{'TagValue': 'orphan'}, {'TagKey': 'Env', 'TagValue': 'prod'}]) == {'Env': 'prod'}


class TestParseTagSuppression:
    def test_full_suppress_true(self):
        full, checks = parse_tag_suppression({'lensix-suppress': 'true'})
        assert full is True
        assert checks == frozenset()

    def test_full_suppress_is_case_insensitive(self):
        full, _ = parse_tag_suppression({'lensix-suppress': 'True'})
        assert full is True

    def test_full_suppress_requires_exactly_true(self):
        full, _ = parse_tag_suppression({'lensix-suppress': 'yes'})
        assert full is False

    def test_no_suppression_tags_at_all(self):
        full, checks = parse_tag_suppression({'Name': 'web-1'})
        assert full is False
        assert checks == frozenset()

    def test_single_check_id(self):
        _, checks = parse_tag_suppression({'lensix-suppress-checks': 'ec2_deletion_protection'})
        assert checks == frozenset({'ec2_deletion_protection'})

    def test_multiple_check_ids_hyphen_separated(self):
        _, checks = parse_tag_suppression({'lensix-suppress-checks': 'ec2_deletion_protection-ec2_public_ip'})
        assert checks == frozenset({'ec2_deletion_protection', 'ec2_public_ip'})

    def test_aws_list_of_dicts_shape(self):
        full, checks = parse_tag_suppression([
            {'Key': 'lensix-suppress-checks', 'Value': 'ec2_public_ip'},
        ])
        assert full is False
        assert checks == frozenset({'ec2_public_ip'})

    def test_both_tags_present(self):
        full, checks = parse_tag_suppression({
            'lensix-suppress': 'true', 'lensix-suppress-checks': 'ec2_public_ip',
        })
        assert full is True
        assert checks == frozenset({'ec2_public_ip'})

    def test_none_tags(self):
        full, checks = parse_tag_suppression(None)
        assert full is False
        assert checks == frozenset()

    def test_empty_checks_value_yields_no_checks(self):
        _, checks = parse_tag_suppression({'lensix-suppress-checks': ''})
        assert checks == frozenset()

    def test_stray_hyphens_do_not_produce_empty_check_ids(self):
        _, checks = parse_tag_suppression({'lensix-suppress-checks': '-ec2_public_ip--'})
        assert checks == frozenset({'ec2_public_ip'})


class TestAddResourceTagSuppression:
    def test_fully_suppressed_resource_is_not_recorded(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'}, tags={'lensix-suppress': 'true'})
        assert w.records == []

    def test_fully_suppressed_resource_does_not_count_toward_resource_counts(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={}, tags={'lensix-suppress': 'true'})
        assert w.resource_counts == {}

    def test_fully_suppressed_resource_is_tracked_in_tag_suppressions(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={}, tags={'lensix-suppress': 'true'})
        assert w.tag_suppressions == [{
            'resource_type': 'ec2_instance', 'resource_id': 'i-1', 'region': 'us-east-1',
            'full_suppress': True, 'check_ids': [],
        }]

    def test_per_check_suppressed_resource_is_still_recorded(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'},
                        tags={'lensix-suppress-checks': 'ec2_deletion_protection'})
        assert len(w.records) == 1
        assert w.records[0]['resource_id'] == 'i-1'

    def test_per_check_suppressed_resource_gets_suppressed_check_ids_in_raw(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'},
                        tags={'lensix-suppress-checks': 'ec2_deletion_protection-ec2_public_ip'})
        # A plain sorted list, not a frozenset — this raw dict is
        # JSON-serialized verbatim for the upload path, and json.dumps
        # has no native frozenset support.
        assert w.records[0]['raw']['_SuppressedCheckIds'] == ['ec2_deletion_protection', 'ec2_public_ip']

    def test_per_check_suppression_does_not_mutate_the_callers_own_raw_dict(self):
        original_raw = {'InstanceId': 'i-1'}
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw=original_raw, tags={'lensix-suppress-checks': 'ec2_public_ip'})
        assert '_SuppressedCheckIds' not in original_raw

    def test_per_check_suppressed_resource_is_tracked_in_tag_suppressions(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={}, tags={'lensix-suppress-checks': 'ec2_public_ip'})
        assert w.tag_suppressions == [{
            'resource_type': 'ec2_instance', 'resource_id': 'i-1', 'region': 'us-east-1',
            'full_suppress': False, 'check_ids': ['ec2_public_ip'],
        }]

    def test_a_resource_with_no_suppression_tags_is_recorded_normally_and_untracked(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'}, tags={'Name': 'web-1'})
        assert len(w.records) == 1
        assert '_SuppressedCheckIds' not in w.records[0]['raw']
        assert w.tag_suppressions == []

    def test_no_tags_argument_at_all_behaves_exactly_as_before(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'})
        assert len(w.records) == 1
        assert w.tag_suppressions == []

    def test_aws_shaped_tags_work_end_to_end(self):
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'},
                        tags=[{'Key': 'lensix-suppress', 'Value': 'true'}])
        assert w.records == []
        assert w.tag_suppressions[0]['full_suppress'] is True

    def test_tag_suppressions_property_returns_a_copy(self):
        w = _writer()
        w.add_resource(resource_type='a', region='r', resource_id='1', resource_name='1',
                        raw={}, tags={'lensix-suppress': 'true'})
        snapshot = w.tag_suppressions
        snapshot.append({'fake': True})
        assert len(w.tag_suppressions) == 1


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

    def test_suppressed_check_ids_survive_the_json_round_trip_as_a_list(self, tmp_path):
        # Regression test: raw['_SuppressedCheckIds'] used to be injected
        # as a frozenset, which json.dumps has no native support for —
        # _json_default's str(value) fallback silently mangled it into a
        # single unusable string like "frozenset({'ec2_deletion_protection'})"
        # instead of a real JSON array, breaking every downstream consumer
        # of a customer's own uploaded inventory file (derive_tag_
        # suppressions(), each check-evaluation loop's `in` check).
        w = _writer()
        w.add_resource(resource_type='ec2_instance', region='us-east-1', resource_id='i-1',
                        resource_name='i-1', raw={'InstanceId': 'i-1'},
                        tags={'lensix-suppress-checks': 'ec2_deletion_protection-ec2_public_ip'})
        path = tmp_path / 'out.ndjson.gz'
        w.write(str(path))

        lines = self._read_lines(str(path))
        resource_line = next(l for l in lines if l['kind'] == 'resource')
        assert resource_line['raw']['_SuppressedCheckIds'] == ['ec2_deletion_protection', 'ec2_public_ip']

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
