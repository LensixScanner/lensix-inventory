"""Unit tests for functions.py — Cloud Functions (v2).

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, plus a light
check that tag wiring didn't disturb the pre-existing env-var secret-scrub
behavior.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.functions as m


def _function(*, name='projects/p/locations/us-central1/functions/fn1', labels=None,
              svc_env=None, build_env=None):
    d = {'name': name, 'serviceConfig': {}, 'buildConfig': {}}
    if labels is not None:
        d['labels'] = labels
    if svc_env is not None:
        d['serviceConfig']['environmentVariables'] = svc_env
    if build_env is not None:
        d['buildConfig']['environmentVariables'] = build_env
    return d


def _cf_client(functions, iam_by_name=None):
    cf = MagicMock()
    req = MagicMock()
    req.execute.return_value = {'functions': functions}
    cf.projects.return_value.locations.return_value.functions.return_value.list.return_value = req

    iam_by_name = iam_by_name or {}

    def _iam(resource):
        r = MagicMock()
        r.execute.return_value = iam_by_name.get(resource, {'bindings': []})
        return r
    cf.projects.return_value.locations.return_value.functions.return_value.getIamPolicy.side_effect = _iam
    return cf


class TestGather:
    def test_adds_one_resource_per_function(self):
        fn = _function()
        cf = _cf_client([fn])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=cf):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'cloud_function'
        assert kwargs['region'] == 'us-central1'
        assert kwargs['resource_name'] == 'fn1'
        assert kwargs['tags'] is None

    def test_tags_are_passed_through_for_suppression(self):
        fn = _function(labels={'lensix-suppress': 'true'})
        cf = _cf_client([fn])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=cf):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_env_vars_are_still_stripped_to_names_only_with_tags_wired(self):
        fn = _function(labels={'env': 'prod'}, svc_env={'API_KEY': 'sk-live-hardcoded'})
        cf = _cf_client([fn])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=cf):
            m.gather('p', MagicMock(), writer)
        raw = writer.add_resource.call_args.kwargs['raw']
        assert 'environmentVariables' not in raw['serviceConfig']
        assert raw['serviceConfig']['environmentVariableNames'] == ['API_KEY']
        assert writer.add_resource.call_args.kwargs['tags'] == {'env': 'prod'}

    def test_a_functions_list_failure_is_isolated_and_gather_returns_without_raising(self):
        cf = MagicMock()
        cf.projects.return_value.locations.return_value.functions.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=cf):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_no_functions_adds_nothing(self):
        cf = _cf_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=cf):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
