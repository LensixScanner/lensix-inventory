"""Unit tests for lensix_inventory.azure.synapse — Synapse workspaces."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.synapse as m


def _workspace(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Synapse/workspaces/w1', name='w1'):
    ws = MagicMock()
    ws.location = location
    ws.id = rid
    ws.name = name
    ws.as_dict.return_value = {'id': rid, 'name': name}
    return ws


class TestGather:
    def test_adds_one_resource_per_workspace(self):
        w = MagicMock()
        ws = _workspace()
        client = MagicMock()
        client.workspaces.list.return_value = [ws]
        with patch('azure.mgmt.synapse.SynapseManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='synapse_workspace', region='eastus', resource_id=ws.id,
            resource_name='w1', scope_id='my-rg', raw={'id': ws.id, 'name': 'w1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        ws = _workspace()
        ws.as_dict.return_value = {'id': ws.id, 'name': 'w1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.workspaces.list.return_value = [ws]
        with patch('azure.mgmt.synapse.SynapseManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        ws = _workspace(location=None)
        client = MagicMock()
        client.workspaces.list.return_value = [ws]
        with patch('azure.mgmt.synapse.SynapseManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        client = MagicMock()
        client.workspaces.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.synapse.SynapseManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'synapse:workspaces'
        w.add_resource.assert_not_called()

    def test_no_workspaces_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.workspaces.list.return_value = []
        with patch('azure.mgmt.synapse.SynapseManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
