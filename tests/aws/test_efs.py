"""Unit tests for lensix_inventory.aws.efs — EFS file systems."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.efs as m


def _efs(file_systems):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'FileSystems': file_systems}]
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
            resource_id='fs-1', resource_name='shared-data', raw=fs, tags=fs['Tags'],
        )

    def test_no_file_systems_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_efs([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
