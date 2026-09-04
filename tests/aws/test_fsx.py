"""Unit tests for lensix_inventory.aws.fsx — FSx file systems."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.fsx as m


def _fsx(file_systems):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'FileSystems': file_systems}]
    return client


class TestFsName:
    def test_uses_the_name_tag_when_present(self):
        fs = {'FileSystemId': 'fs-1', 'Tags': [{'Key': 'Name', 'Value': 'my-lustre'}]}
        assert m._fs_name(fs) == 'my-lustre'

    def test_falls_back_to_the_filesystem_id_without_a_name_tag(self):
        fs = {'FileSystemId': 'fs-1', 'Tags': [{'Key': 'env', 'Value': 'prod'}]}
        assert m._fs_name(fs) == 'fs-1'

    def test_falls_back_when_no_tags_at_all(self):
        fs = {'FileSystemId': 'fs-1'}
        assert m._fs_name(fs) == 'fs-1'


class TestGather:
    def test_adds_one_resource_per_file_system(self):
        w = MagicMock()
        fs = {
            'FileSystemId': 'fs-1', 'ResourceARN': 'arn:aws:fsx:us-east-1:111111111111:file-system/fs-1',
            'VpcId': 'vpc-123', 'StorageCapacity': 1200, 'StorageType': 'SSD',
            'FileSystemType': 'WINDOWS', 'Tags': [{'Key': 'Name', 'Value': 'shared-drive'}],
        }
        with patch.object(m.boto3, 'client', return_value=_fsx([fs])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='fsx_file_system', region='us-east-1',
            resource_id='arn:aws:fsx:us-east-1:111111111111:file-system/fs-1',
            resource_name='shared-drive', scope_id='vpc-123', raw=fs, tags=fs['Tags'],
        )

    def test_falls_back_to_the_filesystem_id_when_no_arn_is_present(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        with patch.object(m.boto3, 'client', return_value=_fsx([fs])):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['resource_id'] == 'fs-1'

    def test_scope_id_is_none_when_no_vpc_is_present(self):
        w = MagicMock()
        fs = {'FileSystemId': 'fs-1'}
        with patch.object(m.boto3, 'client', return_value=_fsx([fs])):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['scope_id'] is None

    def test_no_file_systems_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_fsx([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
