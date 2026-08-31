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
