"""Unit tests for dns.py — Cloud DNS managed zones.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring (GCP's
`labels` field maps directly to the shared `tags=` kwarg, same flat-dict
shape as Azure's own `tags`).
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.dns as m


def _zone(*, name='prod-zone', labels=None):
    zone = {'name': name}
    if labels is not None:
        zone['labels'] = labels
    return zone


def _dns_client(zones):
    dns = MagicMock()
    list_req = MagicMock()
    list_req.execute.return_value = {'managedZones': zones}
    dns.managedZones.return_value.list.return_value = list_req
    dns.managedZones.return_value.list_next.return_value = None
    return dns


class TestGetManagedZones:
    def test_returns_zones_from_the_response(self):
        zone = _zone()
        dns = _dns_client([zone])
        assert m.get_managed_zones(dns, 'my-proj') == [zone]

    def test_paginates_via_list_next(self):
        z1 = _zone(name='a')
        z2 = _zone(name='b')
        dns = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'managedZones': [z1]}
        req2 = MagicMock()
        req2.execute.return_value = {'managedZones': [z2]}
        dns.managedZones.return_value.list.return_value = req1
        dns.managedZones.return_value.list_next.side_effect = [req2, None]
        assert m.get_managed_zones(dns, 'my-proj') == [z1, z2]


class TestGather:
    def test_adds_one_resource_per_zone(self):
        zone = _zone()
        dns = _dns_client([zone])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=dns):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'dns_zone'
        assert kwargs['resource_id'] == 'prod-zone'
        assert kwargs['tags'] is None

    def test_tags_are_passed_through_from_labels(self):
        zone = _zone(labels={'lensix-suppress': 'true'})
        dns = _dns_client([zone])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=dns):
            m.gather('my-proj', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_list_failure_is_isolated_and_gather_returns_without_raising(self):
        dns = MagicMock()
        dns.managedZones.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=dns):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_no_zones_adds_nothing(self):
        dns = _dns_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=dns):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_not_called()
