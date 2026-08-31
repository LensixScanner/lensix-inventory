"""Unit tests for lensix_inventory.azure.cdn — CDN profiles.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (the per-endpoint fan-out's own isolation), which
was untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.cdn as m


def _profile(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Cdn/profiles/p1', name='p1'):
    profile = MagicMock()
    profile.location = location
    profile.id = rid
    profile.name = name
    profile.as_dict.return_value = {'id': rid, 'name': name}
    return profile


class TestGather:
    def test_adds_one_resource_per_profile(self):
        w = MagicMock()
        profile = _profile()
        cdn_client = MagicMock()
        cdn_client.profiles.list.return_value = [profile]
        cdn_client.endpoints.list_by_profile.return_value = []
        with patch.object(m, 'CdnManagementClient', return_value=cdn_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='cdn_profile', region='eastus', resource_id=profile.id,
            resource_name='p1', scope_id='my-rg',
            raw={'id': profile.id, 'name': 'p1', '_Endpoints': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        profile = _profile()
        profile.as_dict.return_value = {'id': profile.id, 'name': 'p1', 'tags': {'lensix-suppress': 'true'}}
        cdn_client = MagicMock()
        cdn_client.profiles.list.return_value = [profile]
        cdn_client.endpoints.list_by_profile.return_value = []
        with patch.object(m, 'CdnManagementClient', return_value=cdn_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_profiles_gathers_nothing(self):
        w = MagicMock()
        cdn_client = MagicMock()
        cdn_client.profiles.list.return_value = []
        with patch.object(m, 'CdnManagementClient', return_value=cdn_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
