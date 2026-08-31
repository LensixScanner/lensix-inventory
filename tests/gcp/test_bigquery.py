"""Unit tests for bigquery.py — one raw record per dataset.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.bigquery as m


def _ref(dataset_id='ds1'):
    return {'datasetReference': {'datasetId': dataset_id}}


def _dataset(*, dataset_id='ds1', location='US', labels=None):
    d = {'datasetReference': {'datasetId': dataset_id}, 'location': location}
    if labels is not None:
        d['labels'] = labels
    return d


def _bq_client(refs, dataset_by_id=None):
    bq = MagicMock()
    list_req = MagicMock()
    list_req.execute.return_value = {'datasets': refs}
    bq.datasets.return_value.list.return_value = list_req
    bq.datasets.return_value.list_next.return_value = None

    dataset_by_id = dataset_by_id or {}

    def _get(projectId, datasetId):
        r = MagicMock()
        r.execute.return_value = dataset_by_id.get(datasetId, _dataset(dataset_id=datasetId))
        return r
    bq.datasets.return_value.get.side_effect = _get
    return bq


class TestGetDatasetRefs:
    def test_returns_refs_from_the_response(self):
        ref = _ref()
        bq = _bq_client([ref])
        assert m.get_dataset_refs(bq, 'my-proj') == [ref]

    def test_paginates_via_list_next(self):
        r1, r2 = _ref('a'), _ref('b')
        bq = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'datasets': [r1]}
        req2 = MagicMock()
        req2.execute.return_value = {'datasets': [r2]}
        bq.datasets.return_value.list.return_value = req1
        bq.datasets.return_value.list_next.side_effect = [req2, None]
        assert m.get_dataset_refs(bq, 'my-proj') == [r1, r2]


class TestGather:
    def test_adds_one_resource_per_dataset(self):
        ref = _ref()
        bq = _bq_client([ref], {'ds1': _dataset()})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=bq):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'bigquery_dataset'
        assert kwargs['resource_id'] == 'ds1'
        assert kwargs['region'] == 'us'
        assert kwargs['tags'] is None

    def test_tags_are_passed_through_from_labels(self):
        ref = _ref()
        bq = _bq_client([ref], {'ds1': _dataset(labels={'lensix-suppress': 'true'})})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=bq):
            m.gather('my-proj', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_refs_list_failure_is_isolated_and_gather_returns_without_raising(self):
        bq = MagicMock()
        bq.datasets.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=bq):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_get_failure_for_one_dataset_does_not_abort_the_others(self):
        bad, good = _ref('bad'), _ref('good')
        bq = _bq_client([bad, good], {'good': _dataset(dataset_id='good')})

        def _get(projectId, datasetId):
            if datasetId == 'bad':
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = _dataset(dataset_id='good')
            return r
        bq.datasets.return_value.get.side_effect = _get
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=bq):
            m.gather('my-proj', MagicMock(), writer)
        assert writer.add_resource.call_count == 1
        assert writer.add_error.call_count == 1

    def test_no_refs_adds_nothing(self):
        bq = _bq_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=bq):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_not_called()
