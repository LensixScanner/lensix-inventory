"""Unit tests for lensix_inventory.aws.autoscaling — Auto Scaling groups."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.autoscaling as m


def _asg_client(asgs):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'AutoScalingGroups': asgs}]
    return client


class TestGather:
    def test_adds_one_resource_per_group(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:aws:autoscaling:us-east-1:1:asg:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', return_value=_asg_client([asg])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='autoscaling_group', region='us-east-1',
            resource_id='arn:aws:autoscaling:us-east-1:1:asg:1', resource_name='web-asg', raw=asg,
        )

    def test_no_groups_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_asg_client([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
