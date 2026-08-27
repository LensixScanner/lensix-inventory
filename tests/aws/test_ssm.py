"""Unit tests for lensix_inventory.aws.ssm — Parameter Store parameters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ssm as m


def _ssm_client(params):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Parameters': params}]
    return client


class TestGather:
    def test_adds_one_resource_per_parameter_keyed_by_name(self):
        w = MagicMock()
        param = {'Name': '/app/db-host', 'Type': 'String'}
        with patch.object(m.boto3, 'client', return_value=_ssm_client([param])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='ssm_parameter', region='us-east-1',
            resource_id='/app/db-host', resource_name='/app/db-host', raw=param,
        )

    def test_no_parameters_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_ssm_client([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()

    def test_missing_page_key_is_handled(self):
        # describe_parameters can return a page with no 'Parameters' key at all.
        w = MagicMock()
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{}]
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
