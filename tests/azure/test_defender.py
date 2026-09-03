"""Unit tests for lensix_inventory.azure.defender — subscription pricing
plans and the network interfaces IP-forwarding evaluation needs.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. Pricing
(Defender's own subscription-wide plan tier) has no `tags` field on its
own SDK model at all (confirmed — the SDK discards it with a warning if
passed), the same control-plane class as authorization's role_definition/
securitycenter's security_contact — only network_interface is taggable.
"""

from unittest.mock import MagicMock, patch

import pytest

import lensix_inventory.azure.defender as m


def _pricing(rid='/subscriptions/s1/providers/Microsoft.Security/pricings/VirtualMachines', name='VirtualMachines'):
    pricing = MagicMock()
    pricing.id = rid
    pricing.name = name
    return pricing


def _public_ip(rid='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip1',
               ip_address='1.2.3.4'):
    pip = MagicMock()
    pip.id = rid
    pip.properties = MagicMock(ip_address=ip_address)
    return pip


def _nic(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/networkInterfaces/nic1',
         name='nic1', tags=None):
    nic = MagicMock()
    nic.location = location
    nic.id = rid
    nic.name = name
    nic.as_dict = MagicMock(return_value={'id': rid, 'name': name, 'tags': tags})
    return nic


class TestGather:
    def test_adds_one_resource_per_pricing_and_nic(self):
        w = MagicMock()
        sc = MagicMock()
        sc.pricings.list.return_value = [_pricing()]
        network = MagicMock()
        network.network_interfaces.list_all.return_value = [_nic()]
        with patch('lensix_inventory.azure.defender.SecurityCenter', return_value=sc), \
             patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 2
        pricing_call, nic_call = w.add_resource.call_args_list
        assert pricing_call.kwargs['resource_type'] == 'defender_pricing'
        assert 'tags' not in pricing_call.kwargs
        assert nic_call.kwargs['resource_type'] == 'network_interface'
        assert nic_call.kwargs['tags'] is None

    def test_tags_are_passed_through_for_a_nic(self):
        w = MagicMock()
        sc = MagicMock()
        sc.pricings.list.return_value = []
        network = MagicMock()
        network.network_interfaces.list_all.return_value = [_nic(tags={'lensix-suppress-checks': 'defender_ipforwarding'})]
        with patch('lensix_inventory.azure.defender.SecurityCenter', return_value=sc), \
             patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress-checks': 'defender_ipforwarding'}

    def test_pricings_list_failure_is_recorded_and_gather_continues_to_nics(self):
        w = MagicMock()
        sc = MagicMock()
        sc.pricings.list.side_effect = RuntimeError('boom')
        network = MagicMock()
        network.network_interfaces.list_all.return_value = [_nic()]
        with patch('lensix_inventory.azure.defender.SecurityCenter', return_value=sc), \
             patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert any(c.kwargs['source'] == 'defender:pricings' for c in w.add_error.call_args_list)
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['network_interface']

    def test_nics_list_failure_is_recorded_and_pricings_still_gathered(self):
        w = MagicMock()
        sc = MagicMock()
        sc.pricings.list.return_value = [_pricing()]
        network = MagicMock()
        network.network_interfaces.list_all.side_effect = RuntimeError('boom')
        with patch('lensix_inventory.azure.defender.SecurityCenter', return_value=sc), \
             patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert any(c.kwargs['source'] == 'defender:network_interfaces' for c in w.add_error.call_args_list)
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['defender_pricing']

    def test_nothing_gathers_nothing(self):
        w = MagicMock()
        sc = MagicMock()
        sc.pricings.list.return_value = []
        network = MagicMock()
        network.network_interfaces.list_all.return_value = []
        with patch('lensix_inventory.azure.defender.SecurityCenter', return_value=sc), \
             patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGetPublicIpAddresses:
    """Not called from gather() itself — see the module docstring. Mirrors
    TestGetScheduledActions's own happy/empty/error-propagates shape
    (lensix-inventory/tests/aws/test_autoscaling.py) for a new subscription-
    wide lookup function."""

    def test_returns_addresses_from_the_listing(self):
        network = MagicMock()
        network.public_ip_addresses.list_all.return_value = iter([
            _public_ip(rid='pip-1', ip_address='1.1.1.1'),
            _public_ip(rid='pip-2', ip_address='2.2.2.2'),
        ])
        with patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            result = m.get_public_ip_addresses('cred', 'sub-1')
        assert [(p.id, p.properties.ip_address) for p in result] == [
            ('pip-1', '1.1.1.1'), ('pip-2', '2.2.2.2'),
        ]

    def test_no_public_ips_returns_empty_list(self):
        network = MagicMock()
        network.public_ip_addresses.list_all.return_value = iter([])
        with patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network):
            assert m.get_public_ip_addresses('cred', 'sub-1') == []

    def test_lookup_error_propagates(self):
        network = MagicMock()
        network.public_ip_addresses.list_all.side_effect = RuntimeError('boom')
        with patch('lensix_inventory.azure.defender.NetworkManagementClient', return_value=network), \
             pytest.raises(RuntimeError, match='boom'):
            m.get_public_ip_addresses('cred', 'sub-1')
