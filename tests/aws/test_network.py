"""Unit tests for lensix_inventory.aws.network — Elastic IPs."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.network as m


def _ec2(addresses):
    ec2 = MagicMock()
    ec2.describe_addresses.return_value = {'Addresses': addresses}
    return ec2


class TestGather:
    def test_no_eips_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_ec2([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()

    def test_adds_one_resource_per_eip(self):
        w = MagicMock()
        eip = {'PublicIp': '1.2.3.4', 'AllocationId': 'eipalloc-1'}
        with patch.object(m.boto3, 'client', return_value=_ec2([eip])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='elastic_ip', region='us-east-1', resource_id='eipalloc-1',
            resource_name='1.2.3.4', raw=eip, tags=None,
        )

    def test_eip_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        eip = {'PublicIp': '1.2.3.4', 'AllocationId': 'eipalloc-1', 'Tags': tags}
        with patch.object(m.boto3, 'client', return_value=_ec2([eip])):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_unassociated_eip_is_still_gathered(self):
        # "unattached" is server-side evaluation, not a gather-time filter.
        w = MagicMock()
        eip = {'PublicIp': '1.2.3.4', 'AllocationId': 'eipalloc-1'}
        assert 'AssociationId' not in eip
        with patch.object(m.boto3, 'client', return_value=_ec2([eip])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once()

    def test_falls_back_to_public_ip_when_allocation_id_missing(self):
        # EC2-Classic EIPs have no AllocationId.
        w = MagicMock()
        eip = {'PublicIp': '1.2.3.4'}
        with patch.object(m.boto3, 'client', return_value=_ec2([eip])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == '1.2.3.4'
