"""Unit tests for lensix_inventory.gcp.session — project/credential/region
discovery via local Application Default Credentials."""

from unittest.mock import MagicMock, patch

import pytest

import lensix_inventory.gcp.session as m


class TestGetCredentialsAndProjectId:
    def test_delegates_to_google_auth_default_with_the_cloud_platform_scope(self):
        with patch.object(m, 'google_auth_default', return_value=('creds', 'proj-1')) as default:
            assert m.get_credentials_and_project_id() == ('creds', 'proj-1')
        default.assert_called_once_with(scopes=['https://www.googleapis.com/auth/cloud-platform'])


class TestGetProjectId:
    def test_returns_the_resolved_project_id(self):
        with patch.object(m, 'get_credentials_and_project_id', return_value=('creds', 'proj-1')):
            assert m.get_project_id() == 'proj-1'

    def test_raises_when_no_project_id_could_be_resolved(self):
        with patch.object(m, 'get_credentials_and_project_id', return_value=('creds', None)):
            with pytest.raises(ValueError):
                m.get_project_id()


class TestGetCredentials:
    def test_returns_just_the_credentials_half_of_the_pair(self):
        with patch.object(m, 'get_credentials_and_project_id', return_value=('creds', 'proj-1')):
            assert m.get_credentials() == 'creds'


class TestVerifyCredentials:
    def test_calls_projects_get_with_the_given_project_id(self):
        crm = MagicMock()
        with patch.object(m.discovery, 'build', return_value=crm) as build:
            m.verify_credentials('creds', 'proj-1')
        build.assert_called_once_with('cloudresourcemanager', 'v1', credentials='creds')
        crm.projects.return_value.get.assert_called_once_with(projectId='proj-1')
        crm.projects.return_value.get.return_value.execute.assert_called_once()

    def test_propagates_a_failure_from_the_underlying_call(self):
        crm = MagicMock()
        crm.projects.return_value.get.return_value.execute.side_effect = RuntimeError('unauthorized')
        with patch.object(m.discovery, 'build', return_value=crm):
            with pytest.raises(RuntimeError):
                m.verify_credentials('creds', 'proj-1')


class TestGetRegions:
    def test_flattens_paginated_regions(self):
        compute = MagicMock()
        page1 = MagicMock()
        page1.execute.return_value = {'items': [{'name': 'us-central1'}]}
        page2 = MagicMock()
        page2.execute.return_value = {'items': [{'name': 'europe-west1'}]}
        compute.regions.return_value.list.return_value = page1
        compute.regions.return_value.list_next.side_effect = [page2, None]
        with patch.object(m.discovery, 'build', return_value=compute):
            assert m.get_regions('creds', 'proj-1') == ['us-central1', 'europe-west1']

    def test_empty_result(self):
        compute = MagicMock()
        req = MagicMock()
        req.execute.return_value = {'items': []}
        compute.regions.return_value.list.return_value = req
        compute.regions.return_value.list_next.return_value = None
        with patch.object(m.discovery, 'build', return_value=compute):
            assert m.get_regions('creds', 'proj-1') == []


class TestGetZones:
    def test_flattens_paginated_zones(self):
        compute = MagicMock()
        page1 = MagicMock()
        page1.execute.return_value = {'items': [{'name': 'us-central1-a'}]}
        compute.zones.return_value.list.return_value = page1
        compute.zones.return_value.list_next.return_value = None
        with patch.object(m.discovery, 'build', return_value=compute):
            assert m.get_zones('creds', 'proj-1') == ['us-central1-a']
