"""Unit tests for lensix_inventory.azure.conditionalaccess."""

import json
from unittest.mock import MagicMock, patch

import lensix_inventory.azure.conditionalaccess as m


def _token(credential, value='tok123'):
    credential.get_token.return_value = MagicMock(token=value)
    return credential


def _policy(*, policy_id='p1', name='Require MFA'):
    return {'id': policy_id, 'displayName': name}


class TestGraphGet:
    def test_returns_parsed_json(self):
        resp = MagicMock()
        resp.read.return_value = json.dumps({'value': [1, 2, 3]}).encode()
        resp.__enter__.return_value = resp
        with patch.object(m.urllib.request, 'urlopen', return_value=resp):
            assert m._graph_get('tok', '/identity/conditionalAccess/policies') == {'value': [1, 2, 3]}

    def test_sends_a_bearer_auth_header(self):
        resp = MagicMock()
        resp.read.return_value = b'{}'
        resp.__enter__.return_value = resp
        captured = {}

        def _urlopen(req, timeout=30):
            captured['auth'] = req.get_header('Authorization')
            return resp
        with patch.object(m.urllib.request, 'urlopen', side_effect=_urlopen):
            m._graph_get('tok123', '/path')
        assert captured['auth'] == 'Bearer tok123'


class TestGetConditionalAccessPolicies:
    def test_acquires_a_graph_scoped_token_and_returns_the_value_list(self):
        credential = _token(MagicMock())
        with patch.object(m, '_graph_get', return_value={'value': [_policy()]}) as graph_get:
            result = m.get_conditional_access_policies(credential)
        assert result == [_policy()]
        credential.get_token.assert_called_once_with('https://graph.microsoft.com/.default')
        graph_get.assert_called_once_with('tok123', '/identity/conditionalAccess/policies')

    def test_missing_value_key_returns_an_empty_list(self):
        credential = _token(MagicMock())
        with patch.object(m, '_graph_get', return_value={}):
            assert m.get_conditional_access_policies(credential) == []

    def test_a_token_acquisition_failure_propagates(self):
        credential = MagicMock()
        credential.get_token.side_effect = RuntimeError('boom')
        try:
            m.get_conditional_access_policies(credential)
            assert False, 'expected RuntimeError to propagate'
        except RuntimeError:
            pass

    def test_a_graph_get_failure_propagates(self):
        # Deliberately NOT swallowed here — the live scanmodule needs the
        # real exception (to tell a 401/403 needing re-consent apart from
        # anything else); see conditionalaccess_checks.py's own comment.
        credential = _token(MagicMock())
        with patch.object(m, '_graph_get', side_effect=RuntimeError('boom')):
            try:
                m.get_conditional_access_policies(credential)
                assert False, 'expected RuntimeError to propagate'
            except RuntimeError:
                pass


class TestGather:
    def test_adds_one_resource_per_policy(self):
        w = MagicMock()
        credential = _token(MagicMock())
        with patch.object(m, 'get_conditional_access_policies', return_value=[_policy(policy_id='p1', name='Require MFA')]):
            m.gather(credential, 'sub1', w)
        w.add_resource.assert_called_once_with(
            resource_type='conditional_access_policy', region='global',
            resource_id='p1', resource_name='Require MFA', raw=_policy(policy_id='p1', name='Require MFA'),
        )

    def test_falls_back_to_the_policy_id_when_display_name_missing(self):
        w = MagicMock()
        credential = _token(MagicMock())
        with patch.object(m, 'get_conditional_access_policies', return_value=[{'id': 'p1'}]):
            m.gather(credential, 'sub1', w)
        assert w.add_resource.call_args.kwargs['resource_name'] == 'p1'

    def test_a_fetch_failure_is_captured_via_the_writer_not_raised(self):
        w = MagicMock()
        credential = _token(MagicMock())
        with patch.object(m, 'get_conditional_access_policies', side_effect=RuntimeError('boom')):
            m.gather(credential, 'sub1', w)  # should not raise
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'conditionalaccess'
        w.add_resource.assert_not_called()

    def test_no_policies_adds_no_resources(self):
        w = MagicMock()
        credential = _token(MagicMock())
        with patch.object(m, 'get_conditional_access_policies', return_value=[]):
            m.gather(credential, 'sub1', w)
        w.add_resource.assert_not_called()
