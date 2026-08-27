"""Unit tests for lensix_inventory.aws.glue — security configurations and connections."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.glue as m


def _client(sec_configs=None, connections=None, sec_configs_raise=False, connections_raise=False,
            catalog_encryption=None, catalog_encryption_raise=False):
    client = MagicMock()
    if sec_configs_raise:
        client.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
    else:
        client.get_paginator.return_value.paginate.return_value = [{'SecurityConfigurations': sec_configs or []}]

    if connections_raise:
        client.get_connections.side_effect = RuntimeError('boom')
    else:
        client.get_connections.side_effect = [{'ConnectionList': connections or []}]

    if catalog_encryption_raise:
        client.get_data_catalog_encryption_settings.side_effect = RuntimeError('boom')
    else:
        client.get_data_catalog_encryption_settings.return_value = {
            'DataCatalogEncryptionSettings': catalog_encryption or {},
        }
    return client


class TestRedactConnection:
    def test_keeps_only_property_names(self):
        conn = {'Name': 'my-db', 'ConnectionProperties': {'PASSWORD': 'hunter2', 'USERNAME': 'admin'}}
        raw, hits = m._redact_connection(conn)
        assert raw['ConnectionProperties'] == ['PASSWORD', 'USERNAME']

    def test_detects_a_secret_looking_value(self):
        conn = {'Name': 'my-db', 'ConnectionProperties': {'JDBC_CONNECTION_URL': 'postgres://user:hunter2@db.example.com:5432/prod'}}
        raw, hits = m._redact_connection(conn)
        assert hits == ['Database Connection String']

    def test_no_connection_properties_at_all(self):
        conn = {'Name': 'my-db'}
        raw, hits = m._redact_connection(conn)
        assert raw == conn
        assert hits == []

    def test_the_original_connection_dict_is_not_mutated(self):
        conn = {'Name': 'my-db', 'ConnectionProperties': {'PASSWORD': 'hunter2'}}
        m._redact_connection(conn)
        assert conn['ConnectionProperties'] == {'PASSWORD': 'hunter2'}


class TestGather:
    def test_adds_one_resource_per_security_configuration(self):
        w = MagicMock()
        cfg = {'Name': 'sc1', 'EncryptionConfiguration': {}}
        client = _client(sec_configs=[cfg])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['glue_security_config'].kwargs['resource_id'] == 'sc1'

    def test_adds_one_resource_per_connection_with_redacted_properties(self):
        w = MagicMock()
        conn = {'Name': 'my-db', 'ConnectionProperties': {'PASSWORD': 'hunter2'}}
        client = _client(connections=[conn])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        conn_call = calls['glue_connection']
        assert conn_call.kwargs['resource_id'] == 'my-db'
        assert conn_call.kwargs['raw']['ConnectionProperties'] == ['PASSWORD']

    def test_a_security_configurations_failure_does_not_prevent_connections_from_being_gathered(self):
        w = MagicMock()
        conn = {'Name': 'my-db'}
        client = _client(sec_configs_raise=True, connections=[conn])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'glue (security configurations)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'glue_connection' in calls

    def test_a_connections_failure_does_not_prevent_security_configurations_from_being_gathered(self):
        w = MagicMock()
        cfg = {'Name': 'sc1'}
        client = _client(sec_configs=[cfg], connections_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'glue (connections)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'glue_security_config' in calls

    def test_adds_a_synthetic_catalog_encryption_resource(self):
        w = MagicMock()
        settings = {'EncryptionAtRest': {'CatalogEncryptionMode': 'SSE-KMS'}}
        client = _client(catalog_encryption=settings)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        enc_call = calls['glue_catalog_encryption']
        assert enc_call.kwargs['resource_id'] == 'catalog_encryption'
        assert enc_call.kwargs['resource_name'] == 'Data Catalog'
        assert enc_call.kwargs['raw'] == settings

    def test_a_catalog_encryption_failure_is_recorded_and_does_not_prevent_the_others(self):
        w = MagicMock()
        cfg = {'Name': 'sc1'}
        conn = {'Name': 'my-db'}
        client = _client(sec_configs=[cfg], connections=[conn], catalog_encryption_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'glue (catalog encryption)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'glue_catalog_encryption' not in calls
        assert {'glue_security_config', 'glue_connection'} <= calls.keys()

    def test_a_security_configurations_or_connections_failure_does_not_prevent_catalog_encryption(self):
        w = MagicMock()
        settings = {'EncryptionAtRest': {'CatalogEncryptionMode': 'DISABLED'}}
        client = _client(sec_configs_raise=True, connections_raise=True, catalog_encryption=settings)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'glue_catalog_encryption' in calls
