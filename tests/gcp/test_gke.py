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
    # discovery.build() is patched to return this SAME mock for every
    # service name, including gather()'s own separate 'compute' build for
    # network/subnet edge resolution (see gather()'s own comment) — mocked
    # to terminate immediately (empty results) so tests not exercising
    # those edges don't need to know about this at all, and so the real
    # pagination loop in vpc.get_network_selflink_by_name/
    # get_subnet_selflink_by_key doesn't spin forever against an
    # unconfigured MagicMock's own always-truthy *_next() return.
    container.networks.return_value.list.return_value.execute.return_value = {'items': []}
    container.networks.return_value.list_next.return_value = None
    container.subnetworks.return_value.aggregatedList.return_value.execute.return_value = {'items': {}}
    container.subnetworks.return_value.aggregatedList_next.return_value = None
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


class TestGatherEdges:
    def test_a_cluster_produces_vpc_network_and_subnet_edges(self):
        cluster = _cluster(location='us-central1-a', node_pools=[])
        cluster['network'] = 'default'
        cluster['subnetwork'] = 'sub1'
        network = {'name': 'default', 'selfLink': 'https://compute.../networks/default'}
        subnet = {'name': 'sub1', 'selfLink': 'https://compute.../regions/us-central1/subnetworks/sub1'}
        container = _container_client([cluster])
        container.networks.return_value.list.return_value.execute.return_value = {'items': [network]}
        container.subnetworks.return_value.aggregatedList.return_value.execute.return_value = {
            'items': {'regions/us-central1': {'subnetworks': [subnet]}}}
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        edges = [c.kwargs for c in writer.add_edge.call_args_list]
        assert {'from_type': 'gke_cluster', 'from_id': 'prod-cluster', 'to_type': 'vpc_network', 'to_id': network['selfLink'], 'relationship': 'in_vpc_network'} in edges
        assert {'from_type': 'gke_cluster', 'from_id': 'prod-cluster', 'to_type': 'subnet', 'to_id': subnet['selfLink'], 'relationship': 'in_subnet'} in edges

    def test_a_regional_clusters_own_location_is_used_directly_as_the_subnet_region(self):
        # location='us-central1' (a region, no zone suffix) for a REGIONAL
        # cluster -- normalize_location_to_region() must leave it alone.
        cluster = _cluster(location='us-central1', node_pools=[])
        cluster['subnetwork'] = 'sub1'
        subnet = {'name': 'sub1', 'selfLink': 'https://compute.../regions/us-central1/subnetworks/sub1'}
        container = _container_client([cluster])
        container.subnetworks.return_value.aggregatedList.return_value.execute.return_value = {
            'items': {'regions/us-central1': {'subnetworks': [subnet]}}}
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        edges = [c.kwargs for c in writer.add_edge.call_args_list]
        assert {'from_type': 'gke_cluster', 'from_id': 'prod-cluster', 'to_type': 'subnet', 'to_id': subnet['selfLink'], 'relationship': 'in_subnet'} in edges

    def test_a_node_pool_produces_an_in_cluster_edge(self):
        cluster = _cluster(node_pools=[_pool('pool-1')])
        container = _container_client([cluster])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        edges = [c.kwargs for c in writer.add_edge.call_args_list]
        assert {'from_type': 'gke_node_pool', 'from_id': 'prod-cluster/pool-1', 'to_type': 'gke_cluster', 'to_id': 'prod-cluster', 'relationship': 'in_cluster'} in edges

    def test_a_cluster_with_no_network_or_subnetwork_produces_no_network_edges(self):
        cluster = _cluster(node_pools=[])
        container = _container_client([cluster])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_an_unresolvable_network_produces_no_edge(self):
        cluster = _cluster(node_pools=[])
        cluster['network'] = 'unknown-network'
        container = _container_client([cluster])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_fully_suppressed_cluster_produces_no_node_pool_edge_either(self):
        cluster = _cluster(resource_labels={'lensix-suppress': 'true'}, node_pools=[_pool('pool-1')])
        container = _container_client([cluster])
        writer = MagicMock()
        writer.add_resource.return_value = False
        with patch.object(m.discovery, 'build', return_value=container):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()
