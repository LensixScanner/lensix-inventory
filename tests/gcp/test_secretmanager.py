"""Unit tests for secretmanager.py."""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.secretmanager as m


def _secret(*, name='projects/p/secrets/prod-db-password'):
    return {'name': name}


def _version(*, name, state='ENABLED', create_time='2026-01-01T00:00:00Z'):
    return {'name': name, 'state': state, 'createTime': create_time}


def _sm_client(secrets, versions_by_secret=None):
    sm = MagicMock()
    list_req = MagicMock()
    list_req.execute.return_value = {'secrets': secrets}
    sm.projects.return_value.secrets.return_value.list.return_value = list_req
    sm.projects.return_value.secrets.return_value.list_next.return_value = None

    versions_by_secret = versions_by_secret or {}

    def _versions_list(parent):
        r = MagicMock()
        r.execute.return_value = {'versions': versions_by_secret.get(parent, [])}
        return r
    sm.projects.return_value.secrets.return_value.versions.return_value.list.side_effect = _versions_list
    sm.projects.return_value.secrets.return_value.versions.return_value.list_next.return_value = None
    return sm


class TestGetSecrets:
    def test_returns_secrets_from_the_response(self):
        secret = _secret()
        sm = _sm_client([secret])
        assert m.get_secrets(sm, 'my-proj') == [secret]

    def test_paginates_via_list_next(self):
        s1 = _secret(name='projects/p/secrets/a')
        s2 = _secret(name='projects/p/secrets/b')
        sm = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'secrets': [s1]}
        req2 = MagicMock()
        req2.execute.return_value = {'secrets': [s2]}
        sm.projects.return_value.secrets.return_value.list.return_value = req1
        sm.projects.return_value.secrets.return_value.list_next.side_effect = [req2, None]
        assert m.get_secrets(sm, 'my-proj') == [s1, s2]


class TestGetVersions:
    def test_returns_versions_for_the_secret(self):
        versions = [_version(name='projects/p/secrets/s1/versions/1')]
        sm = _sm_client([], versions_by_secret={'projects/p/secrets/s1': versions})
        assert m.get_versions(sm, 'projects/p/secrets/s1') == versions


class TestGather:
    def test_adds_one_resource_per_secret(self):
        secret = _secret(name='projects/p/secrets/prod-db-password')
        sm = _sm_client([secret])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sm):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'secretmanager_secret'
        assert kwargs['region'] == 'global'
        assert kwargs['resource_name'] == 'prod-db-password'

    def test_merges_versions_into_raw(self):
        secret = _secret(name='projects/p/secrets/s1')
        versions = [_version(name='projects/p/secrets/s1/versions/1'), _version(name='projects/p/secrets/s1/versions/2')]
        sm = _sm_client([secret], versions_by_secret={'projects/p/secrets/s1': versions})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sm):
            m.gather('p', MagicMock(), writer)
        raw = writer.add_resource.call_args.kwargs['raw']
        assert raw['_Versions'] == versions

    def test_a_secrets_list_failure_is_isolated_and_gather_returns_without_raising(self):
        sm = MagicMock()
        sm.projects.return_value.secrets.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sm):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_versions_list_failure_for_one_secret_does_not_abort_the_others_and_defaults_to_empty(self):
        bad = _secret(name='projects/p/secrets/bad')
        good = _secret(name='projects/p/secrets/good')
        sm = _sm_client([bad, good])

        def _versions_list(parent):
            if 'bad' in parent:
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = {'versions': []}
            return r
        sm.projects.return_value.secrets.return_value.versions.return_value.list.side_effect = _versions_list

        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sm):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        assert writer.add_error.call_count == 1
        bad_raw = [c.kwargs['raw'] for c in writer.add_resource.call_args_list if c.kwargs['resource_name'] == 'bad'][0]
        assert bad_raw['_Versions'] == []

    def test_no_secrets_adds_nothing(self):
        sm = _sm_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=sm):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
        writer.add_error.assert_not_called()
