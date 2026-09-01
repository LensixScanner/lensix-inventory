"""Unit tests for lensix_inventory.aws.backup."""

from unittest.mock import MagicMock, patch

import pytest

import lensix_inventory.aws.backup as m


class TestGetProtectedResourceArns:
    def test_returns_arns_from_every_page(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {'Results': [{'ResourceArn': 'arn:aws:rds:us-east-1:1:db:a'}]},
            {'Results': [{'ResourceArn': 'arn:aws:redshift:us-east-1:1:namespace:b'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            arns = m.get_protected_resource_arns('us-east-1')
        assert arns == {'arn:aws:rds:us-east-1:1:db:a', 'arn:aws:redshift:us-east-1:1:namespace:b'}

    def test_no_protected_resources_returns_empty_set(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{'Results': []}]
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_protected_resource_arns('us-east-1') == set()

    def test_lookup_error_propagates(self):
        client = MagicMock()
        client.get_paginator.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client), pytest.raises(RuntimeError, match='boom'):
            m.get_protected_resource_arns('us-east-1')
