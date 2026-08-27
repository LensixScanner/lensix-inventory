"""Unit tests for lensix_inventory.aws.workspaces — WorkSpaces, WorkSpaces
IP access groups, and WorkSpaces directories."""

from unittest.mock import MagicMock, patch

import botocore.exceptions

import lensix_inventory.aws.workspaces as m


def _client(workspaces=None, ip_groups=None, directories=None):
    client = MagicMock()

    def _paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_workspaces':
            p.paginate.return_value = [{'Workspaces': workspaces or []}]
        elif op_name == 'describe_ip_groups':
            p.paginate.return_value = [{'Result': ip_groups or []}]
        elif op_name == 'describe_workspace_directories':
            p.paginate.return_value = [{'Directories': directories or []}]
        return p

    client.get_paginator.side_effect = _paginator
    return client


class TestGather:
    def test_adds_one_resource_per_workspace(self):
        w = MagicMock()
        ws = {'WorkspaceId': 'ws-abc123', 'ComputerName': 'WS-ALICE'}
        with patch.object(m.boto3, 'client', return_value=_client(workspaces=[ws])):
            m.gather('us-east-1', w)
        w.add_resource.assert_any_call(
            resource_type='workspace', region='us-east-1',
            resource_id='ws-abc123', resource_name='WS-ALICE', raw=ws,
        )

    def test_falls_back_to_the_workspace_id_without_a_computer_name(self):
        w = MagicMock()
        ws = {'WorkspaceId': 'ws-abc123'}
        with patch.object(m.boto3, 'client', return_value=_client(workspaces=[ws])):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args_list[0].kwargs
        assert kwargs['resource_name'] == 'ws-abc123'

    def test_no_workspaces_gathers_no_workspace_resource(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_client()):
            m.gather('us-east-1', w)
        types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert 'workspace' not in types

    def test_adds_one_resource_per_ip_group(self):
        w = MagicMock()
        group = {'groupId': 'wsipg-1', 'groupName': 'my-group', 'userRules': [{'ipRule': '0.0.0.0/0'}]}
        with patch.object(m.boto3, 'client', return_value=_client(ip_groups=[group])):
            m.gather('us-east-1', w)
        w.add_resource.assert_any_call(
            resource_type='workspaces_ip_group', region='us-east-1',
            resource_id='wsipg-1', resource_name='my-group', raw=group,
        )

    def test_ip_group_falls_back_to_group_id_without_a_name(self):
        w = MagicMock()
        group = {'groupId': 'wsipg-1'}
        with patch.object(m.boto3, 'client', return_value=_client(ip_groups=[group])):
            m.gather('us-east-1', w)
        ipg_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'workspaces_ip_group')
        assert ipg_call.kwargs['resource_name'] == 'wsipg-1'

    def test_adds_one_resource_per_directory(self):
        w = MagicMock()
        directory = {'DirectoryId': 'd-1', 'DirectoryName': 'my-dir', 'ipGroupIds': []}
        with patch.object(m.boto3, 'client', return_value=_client(directories=[directory])):
            m.gather('us-east-1', w)
        w.add_resource.assert_any_call(
            resource_type='workspaces_directory', region='us-east-1',
            resource_id='d-1', resource_name='my-dir', raw=directory,
        )

    def test_directory_falls_back_to_directory_id_without_a_name(self):
        w = MagicMock()
        directory = {'DirectoryId': 'd-1'}
        with patch.object(m.boto3, 'client', return_value=_client(directories=[directory])):
            m.gather('us-east-1', w)
        dir_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'workspaces_directory')
        assert dir_call.kwargs['resource_name'] == 'd-1'

    def test_gathers_all_three_types_together(self):
        w = MagicMock()
        ws = {'WorkspaceId': 'ws-1'}
        group = {'groupId': 'wsipg-1'}
        directory = {'DirectoryId': 'd-1'}
        with patch.object(m.boto3, 'client', return_value=_client(workspaces=[ws], ip_groups=[group], directories=[directory])):
            m.gather('us-east-1', w)
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types == {'workspace', 'workspaces_ip_group', 'workspaces_directory'}

    def test_a_workspaces_fetch_failure_does_not_prevent_ip_groups_or_directories(self):
        w = MagicMock()
        client = _client(ip_groups=[{'groupId': 'wsipg-1'}], directories=[{'DirectoryId': 'd-1'}])
        real_paginator = client.get_paginator.side_effect

        def _paginator(op_name):
            if op_name == 'describe_workspaces':
                raise botocore.exceptions.ClientError(
                    {'Error': {'Code': 'AccessDenied', 'Message': 'boom'}}, 'DescribeWorkspaces')
            return real_paginator(op_name)
        client.get_paginator.side_effect = _paginator

        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'workspaces'
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types == {'workspaces_ip_group', 'workspaces_directory'}

    def test_an_ip_groups_fetch_failure_does_not_prevent_workspaces_or_directories(self):
        w = MagicMock()
        client = _client(workspaces=[{'WorkspaceId': 'ws-1'}], directories=[{'DirectoryId': 'd-1'}])
        real_paginator = client.get_paginator.side_effect

        def _paginator(op_name):
            if op_name == 'describe_ip_groups':
                raise RuntimeError('boom')
            return real_paginator(op_name)
        client.get_paginator.side_effect = _paginator

        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'workspaces:ip_groups'
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types == {'workspace', 'workspaces_directory'}

    def test_a_directories_fetch_failure_does_not_prevent_workspaces_or_ip_groups(self):
        w = MagicMock()
        client = _client(workspaces=[{'WorkspaceId': 'ws-1'}], ip_groups=[{'groupId': 'wsipg-1'}])
        real_paginator = client.get_paginator.side_effect

        def _paginator(op_name):
            if op_name == 'describe_workspace_directories':
                raise RuntimeError('boom')
            return real_paginator(op_name)
        client.get_paginator.side_effect = _paginator

        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'workspaces:directories'
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types == {'workspace', 'workspaces_ip_group'}

    def test_no_ip_groups_or_directories_gathers_nothing_for_them(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_client()):
            m.gather('us-east-1', w)
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types == set()
