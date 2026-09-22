"""Unit tests for sql.py — Cloud SQL instances.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. Cloud SQL's
tags-equivalent field is settings.userLabels, not a top-level `labels`
key — same naming quirk as Cloud Monitoring's AlertPolicy.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.sql as m


def _instance(*, name='prod-db', region='us-central1', user_labels=None, private_network=None, kms_key=None):
    settings = {'ipConfiguration': {}}
    if user_labels is not None:
        settings['userLabels'] = user_labels
    if private_network is not None:
        settings['ipConfiguration']['privateNetwork'] = private_network
    d = {'name': name, 'region': region, 'settings': settings}
    if kms_key is not None:
        d['diskEncryptionConfiguration'] = {'kmsKeyName': kms_key}
    return d


def _sqladmin_client(instances, networks=None):
    sqladmin = MagicMock()
    req = MagicMock()
    req.execute.return_value = {'items': instances}
    sqladmin.instances.return_value.list.return_value = req
    sqladmin.instances.return_value.list_next.return_value = None
    # discovery.build() is patched to return this SAME mock for every
    # service name, including gather()'s own separate 'compute' build for
    # network edge resolution (see gather()'s own comment) — mocked to
    # terminate immediately so tests not exercising those edges don't need
    # to know about this, and so the real pagination loop doesn't spin
    # forever against an unconfigured MagicMock's own always-truthy
    # list_next() return.
    sqladmin.networks.return_value.list.return_value.execute.return_value = {'items': networks or []}
    sqladmin.networks.return_value.list_next.return_value = None
    return sqladmin


class TestGetInstances:
    def test_returns_instances_from_the_response(self):
        instance = _instance()
        sqladmin = _sqladmin_client([instance])
        assert m.get_instances(sqladmin, 'my-proj') == [instance]

    def test_paginates_via_list_next(self):
        i1 = _instance(name='a')
        i2 = _instance(name='b')
        sqladmin = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'items': [i1]}
        req2 = MagicMock()
        req2.execute.return_value = {'items': [i2]}
        sqladmin.instances.return_value.list.return_value = req1
        sqladmin.instances.return_value.list_next.side_effect = [req2, None]
        assert m.get_instances(sqladmin, 'my-proj') == [i1, i2]


class TestGather:
    def test_adds_one_resource_per_instance(self):
        instance = _instance()
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'sql_instance'
        assert kwargs['resource_id'] == 'prod-db'
        assert kwargs['tags'] is None

    def test_tags_are_passed_through_from_settings_userlabels(self):
        instance = _instance(user_labels={'lensix-suppress': 'true'})
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_list_failure_is_isolated_and_gather_returns_without_raising(self):
        sqladmin = MagicMock()
        sqladmin.instances.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_no_instances_adds_nothing(self):
        sqladmin = _sqladmin_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_private_ip_instance_produces_a_vpc_network_edge(self):
        instance = _instance(private_network='/projects/p/global/networks/default')
        network = {'name': 'default', 'selfLink': 'https://compute.../networks/default'}
        sqladmin = _sqladmin_client([instance], networks=[network])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='sql_instance', from_id='prod-db',
            to_type='vpc_network', to_id=network['selfLink'], relationship='in_vpc_network',
        )

    def test_a_public_only_instance_produces_no_edge(self):
        instance = _instance()
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_an_unresolvable_network_produces_no_edge(self):
        instance = _instance(private_network='/projects/p/global/networks/unknown')
        sqladmin = _sqladmin_client([instance], networks=[{'name': 'default', 'selfLink': 'https://compute.../networks/default'}])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_fully_suppressed_instance_produces_no_edge(self):
        instance = _instance(private_network='/projects/p/global/networks/default')
        network = {'name': 'default', 'selfLink': 'https://compute.../networks/default'}
        sqladmin = _sqladmin_client([instance], networks=[network])
        writer = MagicMock()
        writer.add_resource.return_value = False
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_network_resolution_failure_produces_no_edge(self):
        instance = _instance(private_network='/projects/p/global/networks/default')
        sqladmin = _sqladmin_client([instance])
        sqladmin.networks.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'sql_instance (network resolution)' for c in writer.add_error.call_args_list)
        writer.add_edge.assert_not_called()
        writer.add_resource.assert_called_once()

    def test_a_cmek_instance_produces_a_uses_cmek_edge(self):
        kms_key = 'projects/p/locations/us/keyRings/r/cryptoKeys/k'
        instance = _instance(kms_key=kms_key)
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='sql_instance', from_id='prod-db',
            to_type='kms_crypto_key', to_id=kms_key, relationship='uses_cmek',
        )

    def test_a_google_managed_instance_produces_no_cmek_edge(self):
        instance = _instance()
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_fully_suppressed_instance_produces_no_cmek_edge(self):
        kms_key = 'projects/p/locations/us/keyRings/r/cryptoKeys/k'
        instance = _instance(kms_key=kms_key)
        sqladmin = _sqladmin_client([instance])
        writer = MagicMock()
        writer.add_resource.return_value = False
        with patch.object(m.discovery, 'build', return_value=sqladmin):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_edge.assert_not_called()
