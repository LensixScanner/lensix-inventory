"""Unit tests for sql.py — Cloud SQL instances.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. Cloud SQL's
tags-equivalent field is settings.userLabels, not a top-level `labels`
key — same naming quirk as Cloud Monitoring's AlertPolicy.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.sql as m


def _instance(*, name='prod-db', region='us-central1', user_labels=None, private_network=None):
    settings = {'ipConfiguration': {}}
    if user_labels is not None:
        settings['userLabels'] = user_labels
    if private_network is not None:
        settings['ipConfiguration']['privateNetwork'] = private_network
    return {'name': name, 'region': region, 'settings': settings}


def _sqladmin_client(instances):
    sqladmin = MagicMock()
    req = MagicMock()
    req.execute.return_value = {'items': instances}
    sqladmin.instances.return_value.list.return_value = req
    sqladmin.instances.return_value.list_next.return_value = None
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
