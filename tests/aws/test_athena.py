"""Unit tests for lensix_inventory.aws.athena — Athena workgroups."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.athena as m


def _athena_client(pages, detail_by_name=None, detail_error_names=None):
    client = MagicMock()
    client.list_work_groups.side_effect = pages
    detail_by_name = detail_by_name or {}
    detail_error_names = detail_error_names or set()

    def _get(WorkGroup):
        if WorkGroup in detail_error_names:
            raise RuntimeError('boom')
        return {'WorkGroup': detail_by_name[WorkGroup]}
    client.get_work_group.side_effect = _get
    return client


class TestGetWorkgroupNames:
    def test_paginates_via_next_token(self):
        client = _athena_client([
            {'WorkGroups': [{'Name': 'wg1'}], 'NextToken': 'tok'},
            {'WorkGroups': [{'Name': 'wg2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_workgroup_names('us-east-1') == ['wg1', 'wg2']


class TestGather:
    def test_adds_one_resource_per_workgroup_including_disabled_ones(self):
        w = MagicMock()
        wg = {'Name': 'primary', 'State': 'DISABLED'}
        client = _athena_client([{'WorkGroups': [{'Name': 'primary'}]}], detail_by_name={'primary': wg})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='athena_workgroup', region='us-east-1',
            resource_id='primary', resource_name='primary', raw=wg,
        )

    def test_a_get_failure_for_one_workgroup_does_not_abort_the_others(self):
        w = MagicMock()
        client = _athena_client(
            [{'WorkGroups': [{'Name': 'bad'}, {'Name': 'good'}]}],
            detail_by_name={'good': {}}, detail_error_names={'bad'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'athena_workgroup:bad'
        w.add_resource.assert_called_once()

    def test_no_workgroups_gathers_nothing(self):
        w = MagicMock()
        client = _athena_client([{'WorkGroups': []}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
