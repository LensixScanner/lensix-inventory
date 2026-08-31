"""Unit tests for lensix_inventory.azure.monitor — Activity log profiles.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.monitor as m


def _profile(rid='/subscriptions/s1/providers/microsoft.insights/logprofiles/p1', name='p1'):
    profile = MagicMock()
    profile.id = rid
    profile.name = name
    profile.as_dict.return_value = {'id': rid, 'name': name}
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
