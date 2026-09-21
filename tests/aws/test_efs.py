"""Unit tests for lensix_inventory.aws.efs — EFS file systems."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.efs as m


def _efs(file_systems, mount_targets_by_fs=None, sg_by_mt=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'FileSystems': file_systems}]
    mount_targets_by_fs = mount_targets_by_fs or {}
    sg_by_mt = sg_by_mt or {}

    def _paginator(op):
        p = MagicMock()
        if op == 'describe_file_systems':
            p.paginate.return_value = [{'FileSystems': file_systems}]
        elif op == 'describe_mount_targets':
            p.paginate.side_effect = lambda FileSystemId: [{'MountTargets': mount_targets_by_fs.get(FileSystemId, [])}]
        return p
    client.get_paginator.side_effect = _paginator
    client.describe_mount_target_security_groups.side_effect = lambda MountTargetId: {'SecurityGroups': sg_by_mt.get(MountTargetId, [])}
    return client


class TestFsName:
    def test_uses_the_name_tag_when_present(self):
        fs = {'FileSystemId': 'fs-1', 'Tags': [{'Key': 'Name', 'Value': 'shared-data'}]}
        assert m._fs_name(fs) == 'shared-data'

    def test_falls_back_to_the_filesystem_id_without_a_name_tag(self):
        fs = {'FileSystemId': 'fs-1', 'Tags': [{'Key': 'env', 'Value': 'prod'}]}
        assert m._fs_name(fs) == 'fs-1'

    def test_falls_back_when_no_tags_at_all(self):
        fs = {'FileSystemId': 'fs-1'}
        assert m._fs_name(fs) == 'fs-1'


class TestGather:
    def test_adds_one_resource_per_file_system(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1', 'Tags': [{'Key': 'Name', 'Value': 'shared-data'}]}
        with patch.object(m.boto3, 'client', return_value=_efs([fs])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='efs_filesystem', region='us-east-1',
            resource_id='fs-1', resource_name='shared-data', raw={**fs, '_MountTargets': []}, tags=fs['Tags'],
        )

    def test_no_file_systems_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_efs([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()

    def test_mount_targets_and_their_security_groups_are_fused_into_raw(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        mount_target = {'MountTargetId': 'fsmt-1', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1'}
        client = _efs([fs], mount_targets_by_fs={'fs-1': [mount_target]}, sg_by_mt={'fsmt-1': ['sg-1']})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        raw = w.add_resource.call_args.kwargs['raw']
        assert raw['_MountTargets'] == [{'MountTargetId': 'fsmt-1', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1', 'SecurityGroups': ['sg-1']}]

    def test_a_mount_targets_fetch_error_is_captured_and_the_filesystem_still_gathers(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        client = _efs([fs])
        client.get_paginator.side_effect = lambda op: (
            (_ for _ in ()).throw(RuntimeError('boom')) if op == 'describe_mount_targets'
            else MagicMock(paginate=MagicMock(return_value=[{'FileSystems': [fs]}]))
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_args.kwargs['region'] == 'us-east-1'
        raw = w.add_resource.call_args.kwargs['raw']
        assert raw['_MountTargets'] == []

    def test_a_mount_target_produces_subnet_security_group_and_vpc_edges(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        mount_target = {'MountTargetId': 'fsmt-1', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1'}
        client = _efs([fs], mount_targets_by_fs={'fs-1': [mount_target]}, sg_by_mt={'fsmt-1': ['sg-1']})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        edges = [c.kwargs for c in w.add_edge.call_args_list]
        assert {'from_type': 'efs_filesystem', 'from_id': 'fs-1', 'to_type': 'subnet', 'to_id': 'subnet-1', 'relationship': 'in_subnet'} in edges
        assert {'from_type': 'efs_filesystem', 'from_id': 'fs-1', 'to_type': 'security_group', 'to_id': 'sg-1', 'relationship': 'member_of_sg'} in edges
        assert {'from_type': 'efs_filesystem', 'from_id': 'fs-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in edges

    def test_multiple_mount_targets_in_the_same_vpc_produce_only_one_vpc_edge(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        mts = [
            {'MountTargetId': 'fsmt-1', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1'},
            {'MountTargetId': 'fsmt-2', 'SubnetId': 'subnet-2', 'VpcId': 'vpc-1'},
        ]
        client = _efs([fs], mount_targets_by_fs={'fs-1': mts})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        vpc_edges = [c.kwargs for c in w.add_edge.call_args_list if c.kwargs['to_type'] == 'vpc']
        assert len(vpc_edges) == 1

    def test_a_file_system_with_no_mount_targets_produces_no_edges(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        client = _efs([fs])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_file_system_produces_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        fs = {'FileSystemId': 'fs-1'}
        mount_target = {'MountTargetId': 'fsmt-1', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1'}
        client = _efs([fs], mount_targets_by_fs={'fs-1': [mount_target]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_a_security_group_fetch_error_for_one_mount_target_does_not_lose_the_others(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        mt_bad = {'MountTargetId': 'fsmt-bad'}
        mt_good = {'MountTargetId': 'fsmt-good'}
        client = _efs([fs], mount_targets_by_fs={'fs-1': [mt_bad, mt_good]}, sg_by_mt={'fsmt-good': ['sg-1']})
        client.describe_mount_target_security_groups.side_effect = lambda MountTargetId: (
            (_ for _ in ()).throw(RuntimeError('boom')) if MountTargetId == 'fsmt-bad' else {'SecurityGroups': ['sg-1']}
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs.get('source', '').startswith('efs_filesystem:fs-1 (mount target fsmt-bad') for c in w.add_error.call_args_list)
        raw = w.add_resource.call_args.kwargs['raw']
        mt_ids_with_sgs = {mt['MountTargetId']: mt.get('SecurityGroups') for mt in raw['_MountTargets']}
        assert mt_ids_with_sgs['fsmt-good'] == ['sg-1']
        assert 'SecurityGroups' not in next(mt for mt in raw['_MountTargets'] if mt['MountTargetId'] == 'fsmt-bad')
