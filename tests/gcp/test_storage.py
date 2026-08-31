"""Unit tests for storage.py — one merged raw record per bucket.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.storage as m


def _bucket(*, name='prod-assets', location='US', labels=None):
    b = {'name': name, 'location': location}
    if labels is not None:
        b['labels'] = labels
    return b


def _storage_client(buckets, iam_by_name=None):
    storage = MagicMock()
    req = MagicMock()
    req.execute.return_value = {'items': buckets}
    storage.buckets.return_value.list.return_value = req
    storage.buckets.return_value.list_next.return_value = None

    iam_by_name = iam_by_name or {}

    def _iam(bucket):
        r = MagicMock()
        r.execute.return_value = iam_by_name.get(bucket, {'bindings': []})
        return r
    storage.buckets.return_value.getIamPolicy.side_effect = _iam
    return storage


class TestGather:
    def test_adds_one_resource_per_bucket(self):
        bucket = _bucket()
        storage = _storage_client([bucket])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=storage):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'storage_bucket'
        assert kwargs['region'] == 'us'
        assert kwargs['tags'] is None

    def test_tags_are_passed_through_from_labels(self):
        bucket = _bucket(labels={'lensix-suppress': 'true'})
        storage = _storage_client([bucket])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=storage):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_buckets_list_failure_is_isolated_and_gather_returns_without_raising(self):
        storage = MagicMock()
        storage.buckets.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=storage):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_getiampolicy_failure_for_one_bucket_does_not_abort_the_others(self):
        bad = _bucket(name='bad')
        good = _bucket(name='good')
        storage = _storage_client([bad, good])

        def _iam(bucket):
            if bucket == 'bad':
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = {'bindings': []}
            return r
        storage.buckets.return_value.getIamPolicy.side_effect = _iam
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=storage):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        assert writer.add_error.call_count == 1

    def test_no_buckets_adds_nothing(self):
        storage = _storage_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=storage):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
