"""Unit tests for lensix_inventory.azure.appservice — one merged raw
record per App Service app.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. WebSiteManagementClient
is a module-level import here (not deferred like most other Azure gather
modules), so patching goes through this module's own namespace.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.appservice as m


def _app(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Web/sites/app1',
         name='app1', kind='app', tags=None):
    app = MagicMock()
    app.id = rid
    app.location = location
    app.name = name
    app.kind = kind
    app.as_dict.return_value = {'id': rid, 'name': name, 'kind': kind, 'tags': tags}
    return app


def _client(apps):
    client = MagicMock()
    client.web_apps.list.return_value = apps
    client.web_apps.get_configuration.return_value = MagicMock(as_dict=MagicMock(return_value={}))
    client.web_apps.get_auth_settings.return_value = MagicMock(as_dict=MagicMock(return_value={}))
    client.web_apps.list_application_settings.return_value = MagicMock(properties={})
    client.web_apps.list_functions.return_value = []
    return client


class TestGather:
    def test_adds_one_resource_per_app(self):
        w = MagicMock()
        app = _app()
        client = _client([app])
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once()
        call = w.add_resource.call_args
        assert call.kwargs['resource_type'] == 'app_service'
        assert call.kwargs['tags'] is None

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        app = _app(tags={'lensix-suppress': 'true'})
        client = _client([app])
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_functionapp_kind_merges_in_functions(self):
        w = MagicMock()
        app = _app(kind='functionapp,linux')
        client = _client([app])
        client.web_apps.list_functions.return_value = [MagicMock(as_dict=MagicMock(return_value={'name': 'fn1'}))]
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args.kwargs['raw']
        assert raw['_Functions'] == [{'name': 'fn1'}]

    def test_a_non_functionapp_has_no_functions_key(self):
        w = MagicMock()
        app = _app(kind='app')
        client = _client([app])
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args.kwargs['raw']
        assert '_Functions' not in raw

    def test_a_sub_fetch_failure_is_tolerated_not_raised(self):
        w = MagicMock()
        app = _app()
        client = _client([app])
        client.web_apps.get_configuration.side_effect = RuntimeError('boom')
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)  # must not raise
        raw = w.add_resource.call_args.kwargs['raw']
        assert '_error' in raw['_SiteConfig']

    def test_no_apps_gathers_nothing(self):
        w = MagicMock()
        client = _client([])
        with patch.object(m, 'WebSiteManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
