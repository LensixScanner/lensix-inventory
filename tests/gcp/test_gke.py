"""Unit tests for gke.py — clusters and their node pools.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. GKE's
tags-equivalent field is resourceLabels, not a top-level `labels` key —
NodePool has no resource-level labels field at all (its config.labels are
Kubernetes node labels, a different concept), a genuine architectural N/A.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.gke as m


def _pool(name='default-pool'):
    return {'name': name}


def _cluster(*, name='prod-cluster', location='us-central1', resource_labels=None, node_pools=None):
    c = {'name': name, 'location': location, 'nodePools': node_pools if node_pools is not None else [_pool()]}
    if resource_labels is not None:
        c['resourceLabels'] = resource_labels
    return c


def _container_client(clusters):
    container = MagicMock()
    container.projects.return_value.locations.return_value.clusters.return_value.list.return_value.execute.return_value = {
        'clusters': clusters}
    return container


class TestGather:
    def test_adds_a_cluster_and_node_pool_resource(self):
        cluster = _cluster()
        container = _container_client([cluster])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        cluster_call, pool_call = writer.add_resource.call_args_list
        assert cluster_call.kwargs['resource_type'] == 'gke_cluster'
        assert cluster_call.kwargs['tags'] is None
        assert pool_call.kwargs['resource_type'] == 'gke_node_pool'
        assert 'tags' not in pool_call.kwargs

    def test_tags_are_passed_through_for_the_cluster_via_resourcelabels(self):
        cluster = _cluster(resource_labels={'lensix-suppress': 'true'})
        container = _container_client([cluster])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        cluster_call = writer.add_resource.call_args_list[0]
        assert cluster_call.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_clusters_list_failure_is_isolated_and_gather_returns_without_raising(self):
        container = MagicMock()
        container.projects.return_value.locations.return_value.clusters.return_value.list.return_value.execute.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_no_clusters_adds_nothing(self):
        container = _container_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
