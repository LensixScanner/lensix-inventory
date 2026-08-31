"""Unit tests for lensix_inventory.azure.containerapps — Container Apps.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, plus a light
check that tag wiring didn't disturb the pre-existing env-var secret-scrub
behavior (scrubbed before .as_dict() is called — see the module's own
docstring for why).
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.containerapps as m


def _env(name='KEY', value='plain-text-value', secret_ref=None):
    e = MagicMock()
    e.env = None
    e.name = name
    e.value = value
    e.secret_ref = secret_ref
    return e


def _container(env=None):
    c = MagicMock()
    c.env = env or []
    return c


def _app(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.App/containerApps/app1',
         name='app1', tags=None, containers=None, init_containers=None):
    app = MagicMock()
    app.location = location
    app.id = rid
    app.name = name
    app.template = MagicMock(containers=containers or [], init_containers=init_containers or [])
    app.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return app


class TestGather:
    def test_adds_one_resource_per_app(self):
        w = MagicMock()
        app = _app()
        client = MagicMock()
        client.container_apps.list_by_subscription.return_value = [app]
        client.container_apps_auth_configs.get.return_value = MagicMock(as_dict=MagicMock(return_value={}))
        with patch.object(m, 'ContainerAppsAPIClient', return_value=client), patch.object(m, '_IMPORT_OK', True):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once()
        call = w.add_resource.call_args
        assert call.kwargs['resource_type'] == 'container_app'
        assert call.kwargs['tags'] is None

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        app = _app(tags={'lensix-suppress': 'true'})
        client = MagicMock()
        client.container_apps.list_by_subscription.return_value = [app]
        client.container_apps_auth_configs.get.return_value = MagicMock(as_dict=MagicMock(return_value={}))
        with patch.object(m, 'ContainerAppsAPIClient', return_value=client), patch.object(m, '_IMPORT_OK', True):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_env_secrets_are_still_scrubbed_before_as_dict_with_tags_wired(self):
        # Regression guard: tag wiring must not disturb the pre-existing
        # scrub-before-as_dict() ordering documented in the module's own
        # docstring.
        w = MagicMock()
        env = _env(value='hardcoded-plaintext', secret_ref=None)
        app = _app(tags={'env': 'prod'}, containers=[_container(env=[env])])
        client = MagicMock()
        client.container_apps.list_by_subscription.return_value = [app]
        client.container_apps_auth_configs.get.return_value = MagicMock(as_dict=MagicMock(return_value={}))
        with patch.object(m, 'ContainerAppsAPIClient', return_value=client), patch.object(m, '_IMPORT_OK', True):
            m.gather('cred', 'sub-1', w)
        assert env.value is None
        assert w.add_resource.call_args.kwargs['tags'] == {'env': 'prod'}

    def test_import_not_ok_records_an_error_and_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m, '_IMPORT_OK', False), patch.object(m, '_IMPORT_ERR', 'no module', create=True):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'containerapps'
        w.add_resource.assert_not_called()

    def test_no_apps_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.container_apps.list_by_subscription.return_value = []
        with patch.object(m, 'ContainerAppsAPIClient', return_value=client), patch.object(m, '_IMPORT_OK', True):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
