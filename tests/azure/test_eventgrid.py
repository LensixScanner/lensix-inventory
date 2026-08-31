"""Unit tests for lensix_inventory.azure.eventgrid — Event Grid domains.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (e.g. get_diagnostic_settings' own isolation), which
was untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.eventgrid as m


def _domain(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.EventGrid/domains/d1', name='d1'):
    domain = MagicMock()
    domain.location = location
    domain.id = rid
    domain.name = name
    domain.as_dict.return_value = {'id': rid, 'name': name}
    return domain


class TestGather:
    def test_adds_one_resource_per_domain(self):
        w = MagicMock()
        domain = _domain()
        eg_client = MagicMock()
        eg_client.domains.list_by_subscription.return_value = [domain]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'EventGridManagementClient', return_value=eg_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='eventgrid_domain', region='eastus', resource_id=domain.id,
            resource_name='d1', scope_id='my-rg',
            raw={'id': domain.id, 'name': 'd1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        domain = _domain()
        domain.as_dict.return_value = {'id': domain.id, 'name': 'd1', 'tags': {'lensix-suppress': 'true'}}
        eg_client = MagicMock()
        eg_client.domains.list_by_subscription.return_value = [domain]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch.object(m, 'EventGridManagementClient', return_value=eg_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_diagnostic_settings_failure_falls_back_to_empty_list(self):
        w = MagicMock()
        domain = _domain()
        eg_client = MagicMock()
        eg_client.domains.list_by_subscription.return_value = [domain]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.side_effect = RuntimeError('boom')
        with patch.object(m, 'EventGridManagementClient', return_value=eg_client), \
             patch.object(m, 'MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['raw']['_DiagnosticSettings'] == []

    def test_no_domains_gathers_nothing(self):
        w = MagicMock()
        eg_client = MagicMock()
        eg_client.domains.list_by_subscription.return_value = []
        with patch.object(m, 'EventGridManagementClient', return_value=eg_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
