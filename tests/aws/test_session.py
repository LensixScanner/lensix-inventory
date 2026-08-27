"""Unit tests for lensix_inventory.aws.session — account/region discovery
via the local AWS credential chain."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.session as m


class TestGetAccountId:
    def test_returns_the_account_id_from_sts(self):
        sts = MagicMock()
        sts.get_caller_identity.return_value = {'Account': '123456789012', 'UserId': 'AIDA...', 'Arn': 'arn:...'}
        with patch.object(m.boto3, 'client', return_value=sts) as client:
            assert m.get_account_id() == '123456789012'
        client.assert_called_once_with('sts')


class TestGetRegions:
    def test_returns_region_names_filtered_to_enabled_regions(self):
        ec2 = MagicMock()
        ec2.describe_regions.return_value = {
            'Regions': [{'RegionName': 'us-east-1'}, {'RegionName': 'eu-west-1'}],
        }
        with patch.object(m.boto3, 'client', return_value=ec2) as client:
            assert m.get_regions() == ['us-east-1', 'eu-west-1']
        client.assert_called_once_with('ec2', region_name='us-east-1')
        ec2.describe_regions.assert_called_once_with(
            Filters=[{'Name': 'opt-in-status', 'Values': ['opt-in-not-required', 'opted-in']}]
        )

    def test_empty_result_returns_an_empty_list(self):
        ec2 = MagicMock()
        ec2.describe_regions.return_value = {'Regions': []}
        with patch.object(m.boto3, 'client', return_value=ec2):
            assert m.get_regions() == []
