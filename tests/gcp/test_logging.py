"""Unit tests for lensix_inventory.gcp.logging — log buckets/sinks/
log-based metrics/alert policies, a log sink's destination-bucket
existence check, and the project IAM policy (audit configs) reused from
iam.py's own get_iam_policy()."""

from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

import lensix_inventory.gcp.logging as m


def _http_error(status):
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b'{}')


def _clients(*, log_buckets=None, log_sinks=None, log_based_metrics=None,
             alert_policies=None, iam_policy=None, iam_policy_raises=None,
             bucket_get_side_effect=None):
    logging_api = MagicMock()
    logging_api.projects.return_value.locations.return_value.buckets.return_value.list.return_value.execute.return_value = {
        'buckets': log_buckets or []}
    logging_api.projects.return_value.sinks.return_value.list.return_value.execute.return_value = {
        'sinks': log_sinks or []}
    logging_api.projects.return_value.metrics.return_value.list.return_value.execute.return_value = {
        'metrics': log_based_metrics or []}

    monitoring = MagicMock()
    monitoring.projects.return_value.alertPolicies.return_value.list.return_value.execute.return_value = {
        'alertPolicies': alert_policies or []}

    crm = MagicMock()
    if iam_policy_raises:
        crm.projects.return_value.getIamPolicy.return_value.execute.side_effect = iam_policy_raises
    else:
        crm.projects.return_value.getIamPolicy.return_value.execute.return_value = iam_policy or {}

    storage_api = MagicMock()
    if bucket_get_side_effect is not None:
        storage_api.buckets.return_value.get.return_value.execute.side_effect = bucket_get_side_effect

    def _build(service, version, credentials):
        return {'logging': logging_api, 'monitoring': monitoring,
                'cloudresourcemanager': crm, 'storage': storage_api}[service]
    return _build


class TestGetDestinationBucketExists:
    def test_non_gcs_destination_returns_none_without_a_call(self):
        storage_api = MagicMock()
        assert m.get_destination_bucket_exists(storage_api, 'bigquery.googleapis.com/projects/p/datasets/d') is None
        storage_api.buckets.assert_not_called()

    def test_existing_bucket_returns_true(self):
        storage_api = MagicMock()
        storage_api.buckets.return_value.get.return_value.execute.return_value = {}
        assert m.get_destination_bucket_exists(storage_api, 'storage.googleapis.com/my-bucket') is True

    def test_404_returns_false(self):
        storage_api = MagicMock()
        storage_api.buckets.return_value.get.return_value.execute.side_effect = _http_error(404)
        assert m.get_destination_bucket_exists(storage_api, 'storage.googleapis.com/my-bucket') is False

    def test_403_returns_none_not_a_failure(self):
        storage_api = MagicMock()
        storage_api.buckets.return_value.get.return_value.execute.side_effect = _http_error(403)
        assert m.get_destination_bucket_exists(storage_api, 'storage.googleapis.com/my-bucket') is None

    def test_other_http_error_raises(self):
        storage_api = MagicMock()
        storage_api.buckets.return_value.get.return_value.execute.side_effect = _http_error(500)
        try:
            m.get_destination_bucket_exists(storage_api, 'storage.googleapis.com/my-bucket')
            assert False, 'expected HttpError to propagate'
        except HttpError:
            pass


class TestGather:
    def test_adds_one_resource_per_bucket_sink_metric_and_policy(self):
        w = MagicMock()
        build = _clients(
            log_buckets=[{'name': 'projects/p/locations/global/buckets/b1'}],
            log_sinks=[{'name': 'projects/p/sinks/s1', 'destination': 'bigquery.googleapis.com/x'}],
            log_based_metrics=[{'name': 'projects/p/metrics/m1'}],
            alert_policies=[{'name': 'projects/p/alertPolicies/a1', 'displayName': 'My Alert'}],
        )
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['log_bucket'].kwargs['resource_id'] == 'projects/p/locations/global/buckets/b1'
        assert calls['log_sink'].kwargs['resource_id'] == 'projects/p/sinks/s1'
        assert calls['log_based_metric'].kwargs['resource_id'] == 'projects/p/metrics/m1'
        assert calls['alert_policy'].kwargs['resource_name'] == 'My Alert'

    def test_a_sink_with_a_live_destination_bucket_gets_exists_true(self):
        w = MagicMock()
        build = _clients(log_sinks=[{'name': 'projects/p/sinks/s1', 'destination': 'storage.googleapis.com/my-bucket'}])
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['log_sink'].kwargs['raw']['_DestinationBucketExists'] is True

    def test_a_sink_with_a_missing_destination_bucket_gets_exists_false(self):
        w = MagicMock()
        build = _clients(
            log_sinks=[{'name': 'projects/p/sinks/s1', 'destination': 'storage.googleapis.com/gone'}],
            bucket_get_side_effect=_http_error(404),
        )
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['log_sink'].kwargs['raw']['_DestinationBucketExists'] is False

    def test_a_bucket_check_failure_records_none_and_an_error_but_still_gathers_the_sink(self):
        w = MagicMock()
        build = _clients(
            log_sinks=[{'name': 'projects/p/sinks/s1', 'destination': 'storage.googleapis.com/x'}],
            bucket_get_side_effect=_http_error(500),
        )
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        assert any(c.kwargs['source'] == 'log_sink (destination bucket:projects/p/sinks/s1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['log_sink'].kwargs['raw']['_DestinationBucketExists'] is None

    def test_adds_the_iam_policy_resource(self):
        w = MagicMock()
        policy = {'bindings': [], 'auditConfigs': [{'service': 'allServices'}]}
        build = _clients(iam_policy=policy)
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['iam_policy'].kwargs['resource_id'] == 'p/iam'
        assert calls['iam_policy'].kwargs['raw'] == policy

    def test_an_iam_policy_failure_is_isolated_from_the_other_resources(self):
        w = MagicMock()
        build = _clients(
            log_buckets=[{'name': 'projects/p/locations/global/buckets/b1'}],
            iam_policy_raises=RuntimeError('boom'),
        )
        with patch.object(m.discovery, 'build', side_effect=build):
            m.gather('p', MagicMock(), w)
        assert any(c.kwargs['source'] == 'iam_policy' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'log_bucket' in calls
        assert 'iam_policy' not in calls

    def test_a_bucket_list_failure_does_not_prevent_the_others(self):
        w = MagicMock()
        build = _clients(log_sinks=[{'name': 'projects/p/sinks/s1'}])
        logging_api_holder = {}

        def _build(service, version, credentials):
            c = build(service, version, credentials)
            if service == 'logging':
                logging_api_holder['api'] = c
                c.projects.return_value.locations.return_value.buckets.return_value.list.return_value.execute.side_effect = RuntimeError('boom')
            return c
        with patch.object(m.discovery, 'build', side_effect=_build):
            m.gather('p', MagicMock(), w)
        assert any(c.kwargs['source'] == 'log_bucket' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'log_sink' in calls
