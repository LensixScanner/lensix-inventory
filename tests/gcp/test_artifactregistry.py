"""Unit tests for artifactregistry.py."""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.artifactregistry as m


def _repo(*, name='projects/p/locations/us/repositories/prod-images', labels=None):
    r = {'name': name, 'format': 'DOCKER'}
    if labels is not None:
        r['labels'] = labels
    return r


def _ar_client(repos, iam_by_name=None):
    ar = MagicMock()
    list_req = MagicMock()
    list_req.execute.return_value = {'repositories': repos}
    ar.projects.return_value.locations.return_value.repositories.return_value.list.return_value = list_req
    ar.projects.return_value.locations.return_value.repositories.return_value.list_next.return_value = None

    iam_by_name = iam_by_name or {}

    def _iam(resource):
        r = MagicMock()
        r.execute.return_value = iam_by_name.get(resource, {'bindings': []})
        return r
    ar.projects.return_value.locations.return_value.repositories.return_value.getIamPolicy.side_effect = _iam
    return ar


class TestGetRepositories:
    def test_returns_repositories_from_the_response(self):
        repo = _repo()
        ar = _ar_client([repo])
        assert m.get_repositories(ar, 'my-proj') == [repo]

    def test_paginates_via_list_next(self):
        r1 = _repo(name='projects/p/locations/us/repositories/a')
        r2 = _repo(name='projects/p/locations/us/repositories/b')
        ar = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'repositories': [r1]}
        req2 = MagicMock()
        req2.execute.return_value = {'repositories': [r2]}
        ar.projects.return_value.locations.return_value.repositories.return_value.list.return_value = req1
        ar.projects.return_value.locations.return_value.repositories.return_value.list_next.side_effect = [req2, None]
        assert m.get_repositories(ar, 'my-proj') == [r1, r2]

    def test_uses_the_location_wildcard(self):
        ar = _ar_client([])
        m.get_repositories(ar, 'my-proj')
        ar.projects.return_value.locations.return_value.repositories.return_value.list.assert_called_with(
            parent='projects/my-proj/locations/-'
        )


class TestGetIamPolicy:
    def test_returns_bindings(self):
        ar = _ar_client([], iam_by_name={'repo1': {'bindings': [{'role': 'roles/artifactregistry.reader', 'members': ['allUsers']}]}})
        assert m.get_iam_policy(ar, 'repo1') == [{'role': 'roles/artifactregistry.reader', 'members': ['allUsers']}]

    def test_defaults_to_empty_list(self):
        ar = _ar_client([], iam_by_name={'repo1': {}})
        assert m.get_iam_policy(ar, 'repo1') == []


class TestRegionFromRepoName:
    def test_extracts_the_location_segment(self):
        assert m._region_from_repo_name('projects/p/locations/us-docker/repositories/r1') == 'us-docker'

    def test_falls_back_to_global_for_a_malformed_name(self):
        assert m._region_from_repo_name('not-a-resource-name') == 'global'


class TestGather:
    def test_adds_one_resource_per_repo_with_region_from_name(self):
        repo = _repo(name='projects/p/locations/us/repositories/prod-images')
        ar = _ar_client([repo])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'artifactregistry_repository'
        assert kwargs['region'] == 'us'
        assert kwargs['resource_name'] == 'prod-images'
        assert kwargs['resource_id'] == 'projects/p/locations/us/repositories/prod-images'

    def test_merges_iam_bindings_into_raw(self):
        repo = _repo(name='projects/p/locations/us/repositories/prod-images')
        bindings = [{'role': 'roles/artifactregistry.reader', 'members': ['allUsers']}]
        ar = _ar_client([repo], iam_by_name={'projects/p/locations/us/repositories/prod-images': {'bindings': bindings}})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        raw = writer.add_resource.call_args.kwargs['raw']
        assert raw['_IamPolicyBindings'] == bindings

    def test_a_repositories_list_failure_is_isolated_and_gather_returns_without_raising(self):
        ar = MagicMock()
        ar.projects.return_value.locations.return_value.repositories.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_getiampolicy_failure_for_one_repo_does_not_abort_the_others(self):
        bad = _repo(name='projects/p/locations/us/repositories/bad')
        good = _repo(name='projects/p/locations/us/repositories/good')
        ar = _ar_client([bad, good])

        def _iam(resource):
            if 'bad' in resource:
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = {'bindings': []}
            return r
        ar.projects.return_value.locations.return_value.repositories.return_value.getIamPolicy.side_effect = _iam

        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        assert writer.add_error.call_count == 1

    def test_tags_are_passed_through_from_labels(self):
        repo = _repo(labels={'lensix-suppress': 'true'})
        ar = _ar_client([repo])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_repos_adds_nothing(self):
        ar = _ar_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=ar):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
        writer.add_error.assert_not_called()
