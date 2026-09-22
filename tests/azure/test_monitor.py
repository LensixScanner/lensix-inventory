"""Unit tests for lensix_inventory.azure.monitor — Activity log profiles.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.monitor as m


def _profile(rid='/subscriptions/s1/providers/microsoft.insights/logprofiles/p1', name='p1', storage_account_id=None):
    profile = MagicMock()
    profile.id = rid
    profile.name = name
    raw = {'id': rid, 'name': name}
    if storage_account_id is not None:
        raw['storage_account_id'] = storage_account_id
    profile.as_dict.return_value = raw
    return profile


class TestGather:
    def test_adds_one_resource_per_profile(self):
        w = MagicMock()
        profile = _profile()
        client = MagicMock()
        client.log_profiles.list.return_value = [profile]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='monitor_log_profile', region='global', resource_id=profile.id,
            resource_name='p1', raw={'id': profile.id, 'name': 'p1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        profile = _profile()
        profile.as_dict.return_value = {'id': profile.id, 'name': 'p1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.log_profiles.list.return_value = [profile]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        client = MagicMock()
        client.log_profiles.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'monitor:log_profiles'
        w.add_resource.assert_not_called()

    def test_no_profiles_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.log_profiles.list.return_value = []
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_profiles_export_storage_account_gets_an_exports_to_edge(self):
        w = MagicMock()
        profile = _profile(storage_account_id='/subscriptions/s1/.../storageAccounts/sa1')
        client = MagicMock()
        client.log_profiles.list.return_value = [profile]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='monitor_log_profile', from_id=profile.id, to_type='storage_account',
            to_id='/subscriptions/s1/.../storageAccounts/sa1', relationship='exports_to',
        )

    def test_a_profile_with_no_storage_account_gets_no_edge(self):
        w = MagicMock()
        profile = _profile()
        client = MagicMock()
        client.log_profiles.list.return_value = [profile]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_profile_gets_no_edge(self):
        w = MagicMock()
        w.add_resource.return_value = False
        profile = _profile(storage_account_id='/subscriptions/s1/.../storageAccounts/sa1')
        client = MagicMock()
        client.log_profiles.list.return_value = [profile]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
