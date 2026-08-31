"""Unit tests for lensix_inventory.azure.frontdoor — Front Door (classic) profiles."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.frontdoor as m


def _front_door(location='global', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/frontDoors/fd1', name='fd1'):
    fd = MagicMock()
    fd.location = location
    fd.id = rid
    fd.name = name
    fd.as_dict.return_value = {'id': rid, 'name': name}
    return fd


class TestGather:
    def test_adds_one_resource_per_front_door(self):
        w = MagicMock()
        fd = _front_door()
        client = MagicMock()
        client.front_doors.list.return_value = [fd]
        with patch.object(m, 'FrontDoorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='frontdoor_profile', region='global', resource_id=fd.id,
            resource_name='fd1', scope_id='my-rg', raw={'id': fd.id, 'name': 'fd1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        fd = _front_door()
        fd.as_dict.return_value = {'id': fd.id, 'name': 'fd1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.front_doors.list.return_value = [fd]
        with patch.object(m, 'FrontDoorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_when_location_attribute_is_entirely_absent(self):
        # Uses getattr(fd, 'location', None), unlike most other Azure
        # modules' plain `.location` access — worth covering the case
        # where the attribute doesn't exist on the model at all.
        w = MagicMock()
        fd = MagicMock(spec=['id', 'name', 'as_dict'])
        fd.id = 'arn:1'
        fd.name = 'fd1'
        fd.as_dict.return_value = {'id': 'arn:1', 'name': 'fd1'}
        client = MagicMock()
        client.front_doors.list.return_value = [fd]
        with patch.object(m, 'FrontDoorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_no_front_doors_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.front_doors.list.return_value = []
        with patch.object(m, 'FrontDoorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
