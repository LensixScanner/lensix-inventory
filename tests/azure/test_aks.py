"""Unit tests for lensix_inventory.azure.aks — AKS managed clusters.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (get_diagnostic_settings' own isolation), which was
untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.aks as m


def _cluster(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.ContainerService/managedClusters/c1', name='c1'):
    cluster = MagicMock()
    cluster.location = location
    cluster.id = rid
    cluster.name = name
    cluster.as_dict.return_value = {'id': rid, 'name': name}
    return cluster


class TestGather:
    def test_adds_one_resource_per_cluster(self):
        w = MagicMock()
        cluster = _cluster()
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = [cluster]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='kubernetes_cluster', region='eastus', resource_id=cluster.id,
            resource_name='c1', scope_id='my-rg',
            raw={'id': cluster.id, 'name': 'c1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = _cluster()
        cluster.as_dict.return_value = {'id': cluster.id, 'name': 'c1', 'tags': {'lensix-suppress': 'true'}}
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = [cluster]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_clusters_gathers_nothing(self):
        w = MagicMock()
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherSubnetEdges:
    def test_a_cluster_gets_an_in_subnet_edge_per_distinct_pool_subnet(self):
        w = MagicMock()
        cluster = _cluster()
        cluster.as_dict.return_value = {
            'id': cluster.id, 'name': 'c1',
            'agent_pool_profiles': [
                {'name': 'pool1', 'vnet_subnet_id': 'subnet-1'},
                {'name': 'pool2', 'vnet_subnet_id': 'subnet-2'},
                {'name': 'pool3', 'vnet_subnet_id': 'subnet-1'},
            ],
        }
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = [cluster]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        edge_calls = w.add_edge.call_args_list
        to_ids = {c.kwargs['to_id'] for c in edge_calls}
        assert to_ids == {'subnet-1', 'subnet-2'}
        for c in edge_calls:
            assert c.kwargs['from_type'] == 'kubernetes_cluster'
            assert c.kwargs['from_id'] == cluster.id
            assert c.kwargs['to_type'] == 'subnet'
            assert c.kwargs['relationship'] == 'in_subnet'

    def test_a_cluster_with_no_pool_subnets_gets_no_edges(self):
        w = MagicMock()
        cluster = _cluster()
        cluster.as_dict.return_value = {
            'id': cluster.id, 'name': 'c1',
            'agent_pool_profiles': [{'name': 'pool1', 'vnet_subnet_id': None}],
        }
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = [cluster]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_cluster_gets_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        cluster = _cluster()
        cluster.as_dict.return_value = {
            'id': cluster.id, 'name': 'c1',
            'agent_pool_profiles': [{'name': 'pool1', 'vnet_subnet_id': 'subnet-1'}],
        }
        aks_client = MagicMock()
        aks_client.managed_clusters.list.return_value = [cluster]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'ContainerServiceClient', return_value=aks_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
