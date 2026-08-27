"""Unit tests for lensix_inventory.aws.ec2 — EC2 instances, network
interfaces, and launch templates."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ec2 as m


def _instance(iid='i-1', state='running', tags=None, vpc_id=None):
    d = {'InstanceId': iid, 'State': {'Name': state}}
    if tags is not None:
        d['Tags'] = tags
    if vpc_id is not None:
        d['VpcId'] = vpc_id
    return d


def _client(instances=None, statuses=None, termination_protection_by_id=None,
            termination_error_ids=None, userdata_by_id=None, enis=None, enis_raise=False,
            launch_templates=None, launch_templates_raise=False,
            lt_versions_by_id=None, lt_version_error_ids=None,
            metric_data_results=None):
    client = MagicMock()
    instances = instances or []
    statuses = statuses or {}
    termination_protection_by_id = termination_protection_by_id or {}
    termination_error_ids = termination_error_ids or set()
    userdata_by_id = userdata_by_id or {}
    lt_versions_by_id = lt_versions_by_id or {}
    lt_version_error_ids = lt_version_error_ids or set()
    if metric_data_results is not None:
        client.get_metric_data.return_value = {'MetricDataResults': metric_data_results}

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_instances':
            p.paginate.return_value = [{'Reservations': [{'Instances': instances}]}]
        elif op_name == 'describe_instance_status':
            def _paginate(InstanceIds, IncludeAllInstances):
                return [{'InstanceStatuses': [
                    {'InstanceId': iid, 'SystemStatus': {'Status': statuses[iid]}}
                    for iid in InstanceIds if iid in statuses
                ]}]
            p.paginate.side_effect = _paginate
        elif op_name == 'describe_network_interfaces':
            if enis_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'NetworkInterfaces': enis or []}]
        elif op_name == 'describe_launch_templates':
            if launch_templates_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'LaunchTemplates': launch_templates or []}]
        return p
    client.get_paginator.side_effect = _get_paginator

    def _describe_attr(InstanceId, Attribute):
        if Attribute == 'disableApiTermination':
            if InstanceId in termination_error_ids:
                raise RuntimeError('boom')
            return {'DisableApiTermination': {'Value': termination_protection_by_id.get(InstanceId, False)}}
        if Attribute == 'userData':
            value = userdata_by_id.get(InstanceId)
            return {'UserData': {'Value': value}} if value else {'UserData': {}}
        raise ValueError('unexpected attribute')
    client.describe_instance_attribute.side_effect = _describe_attr

    def _describe_lt_versions(LaunchTemplateId, Versions):
        if LaunchTemplateId in lt_version_error_ids:
            raise RuntimeError('boom')
        version = lt_versions_by_id.get(LaunchTemplateId)
        return {'LaunchTemplateVersions': [version] if version is not None else []}
    client.describe_launch_template_versions.side_effect = _describe_lt_versions
    return client


class TestTagName:
    def test_uses_the_name_tag(self):
        assert m._tag_name([{'Key': 'Name', 'Value': 'web-1'}], 'i-1') == 'web-1'

    def test_falls_back_without_a_name_tag(self):
        assert m._tag_name([], 'i-1') == 'i-1'

    def test_falls_back_with_no_tags_at_all(self):
        assert m._tag_name(None, 'i-1') == 'i-1'


class TestGetInstanceStatuses:
    def test_returns_empty_dict_for_no_instance_ids(self):
        assert m.get_instance_statuses('us-east-1', []) == {}


class TestGetUserdataSecretHits:
    def test_returns_no_hits_for_clean_userdata(self):
        import base64
        encoded = base64.b64encode(b'#!/bin/bash\necho hello').decode()
        client = _client(userdata_by_id={'i-1': encoded})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_userdata_secret_hits('us-east-1', 'i-1') == []

    def test_detects_a_secret_in_userdata(self):
        import base64
        encoded = base64.b64encode(b'aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY').decode()
        client = _client(userdata_by_id={'i-1': encoded})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_userdata_secret_hits('us-east-1', 'i-1') == ['AWS Secret Access Key']

    def test_no_userdata_returns_no_hits(self):
        client = _client()
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_userdata_secret_hits('us-east-1', 'i-1') == []

    def test_a_fetch_failure_returns_no_hits_rather_than_raising(self):
        client = MagicMock()
        client.describe_instance_attribute.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_userdata_secret_hits('us-east-1', 'i-1') == []


class TestGather:
    def test_adds_one_resource_per_instance_with_status_and_termination_protection_merged_in(self):
        w = MagicMock()
        inst = _instance(iid='i-1', state='running', tags=[{'Key': 'Name', 'Value': 'web-1'}], vpc_id='vpc-1')
        client = _client(instances=[inst], statuses={'i-1': 'ok'}, termination_protection_by_id={'i-1': True})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        instance_call = calls['ec2_instance']
        assert instance_call.kwargs['resource_id'] == 'i-1'
        assert instance_call.kwargs['resource_name'] == 'web-1'
        assert instance_call.kwargs['scope_id'] == 'vpc-1'
        assert instance_call.kwargs['raw']['_SystemStatus'] == 'ok'
        assert instance_call.kwargs['raw']['_DisableApiTermination'] is True

    def test_a_termination_protection_failure_records_none_and_an_error(self):
        w = MagicMock()
        inst = _instance(iid='i-1')
        client = _client(instances=[inst], termination_error_ids={'i-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ec2 (termination protection:i-1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['raw']['_DisableApiTermination'] is None

    def test_userdata_is_only_scanned_for_running_instances(self):
        w = MagicMock()
        import base64
        secret_userdata = base64.b64encode(b'aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY').decode()
        inst = _instance(iid='i-1', state='stopped')
        client = _client(instances=[inst], userdata_by_id={'i-1': secret_userdata})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['secret_scan_hits'] == []

    def test_a_running_instance_with_a_secret_in_userdata_gets_the_hit(self):
        w = MagicMock()
        import base64
        secret_userdata = base64.b64encode(b'aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY').decode()
        inst = _instance(iid='i-1', state='running')
        client = _client(instances=[inst], userdata_by_id={'i-1': secret_userdata})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['secret_scan_hits'] == ['AWS Secret Access Key']

    def test_adds_one_resource_per_network_interface(self):
        w = MagicMock()
        eni = {'NetworkInterfaceId': 'eni-1', 'Description': 'primary', 'VpcId': 'vpc-1'}
        client = _client(enis=[eni])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        eni_call = calls['elastic_network_interface']
        assert eni_call.kwargs['resource_id'] == 'eni-1'
        assert eni_call.kwargs['resource_name'] == 'primary'
        assert eni_call.kwargs['scope_id'] == 'vpc-1'

    def test_falls_back_to_the_eni_id_without_a_description(self):
        w = MagicMock()
        eni = {'NetworkInterfaceId': 'eni-1', 'Description': ''}
        client = _client(enis=[eni])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['elastic_network_interface'].kwargs['resource_name'] == 'eni-1'

    def test_adds_one_resource_per_launch_template_with_its_default_version_merged_in(self):
        w = MagicMock()
        lt = {'LaunchTemplateId': 'lt-1', 'LaunchTemplateName': 'my-lt'}
        version = {'VersionNumber': 3, 'LaunchTemplateData': {'MetadataOptions': {'HttpTokens': 'required'}}}
        client = _client(launch_templates=[lt], lt_versions_by_id={'lt-1': version})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        lt_call = calls['launch_template']
        assert lt_call.kwargs['resource_id'] == 'lt-1'
        assert lt_call.kwargs['resource_name'] == 'my-lt'
        assert lt_call.kwargs['raw']['_DefaultVersion'] == version

    def test_falls_back_to_the_launch_template_id_without_a_name(self):
        w = MagicMock()
        lt = {'LaunchTemplateId': 'lt-1'}
        client = _client(launch_templates=[lt])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['launch_template'].kwargs['resource_name'] == 'lt-1'

    def test_a_missing_default_version_leaves_defaultversion_none(self):
        w = MagicMock()
        lt = {'LaunchTemplateId': 'lt-1', 'LaunchTemplateName': 'my-lt'}
        client = _client(launch_templates=[lt])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['launch_template'].kwargs['raw']['_DefaultVersion'] is None

    def test_a_version_lookup_failure_records_none_and_an_error_but_still_gathers_the_template(self):
        w = MagicMock()
        lt = {'LaunchTemplateId': 'lt-1', 'LaunchTemplateName': 'my-lt'}
        client = _client(launch_templates=[lt], lt_version_error_ids={'lt-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ec2 (launch template version:lt-1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['launch_template'].kwargs['raw']['_DefaultVersion'] is None

    def test_a_launch_templates_list_failure_is_recorded_and_does_not_prevent_instances(self):
        w = MagicMock()
        inst = _instance(iid='i-1')
        client = _client(instances=[inst], launch_templates_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ec2 (launch templates)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ec2_instance' in calls
        assert 'launch_template' not in calls

    def test_a_network_interfaces_failure_does_not_prevent_instances_from_being_gathered(self):
        w = MagicMock()
        inst = _instance(iid='i-1')
        client = _client(instances=[inst], enis_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ec2 (network interfaces)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ec2_instance' in calls

    def test_an_instances_failure_is_not_caught_and_propagates(self):
        w = MagicMock()
        client = MagicMock()
        client.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            try:
                m.gather('us-east-1', w)
                assert False, 'expected the instances failure to propagate'
            except RuntimeError:
                pass

    def test_a_running_instance_gets_metrics_merged_in(self):
        w = MagicMock()
        inst = _instance(iid='i-1', state='running')
        results = [
            {'Id': 'cpu_avg', 'Values': [12.5]}, {'Id': 'cpu_max', 'Values': [40.0]},
            {'Id': 'net_avg', 'Values': [500.0]},
        ]
        client = _client(instances=[inst], metric_data_results=results)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['raw']['_Metrics'] == {'avg_cpu': 12.5, 'max_cpu': 40.0, 'avg_network': 500.0}

    def test_a_stopped_instance_gets_no_metrics_fetch_at_all(self):
        w = MagicMock()
        inst = _instance(iid='i-1', state='stopped')
        client = _client(instances=[inst])
        with patch.object(m, 'get_metrics') as get_metrics, \
             patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        get_metrics.assert_not_called()
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['raw']['_Metrics'] is None

    def test_a_metrics_fetch_failure_records_none_and_an_error(self):
        w = MagicMock()
        inst = _instance(iid='i-1', state='running')
        client = _client(instances=[inst])
        with patch.object(m, 'get_metrics', side_effect=RuntimeError('boom')), \
             patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ec2 (metrics:i-1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ec2_instance'].kwargs['raw']['_Metrics'] is None


class TestGetMetrics:
    def _cw_client(self, results):
        client = MagicMock()
        client.get_metric_data.return_value = {'MetricDataResults': results}
        return client

    def test_maps_each_query_id_to_its_first_value(self):
        results = [
            {'Id': 'cpu_avg', 'Values': [11.1]}, {'Id': 'cpu_max', 'Values': [22.2]},
            {'Id': 'net_avg', 'Values': [33.3]},
        ]
        with patch.object(m.boto3, 'client', return_value=self._cw_client(results)):
            assert m.get_metrics('us-east-1', 'i-1') == {'avg_cpu': 11.1, 'max_cpu': 22.2, 'avg_network': 33.3}

    def test_a_query_with_no_datapoints_maps_to_none(self):
        results = [{'Id': 'cpu_avg', 'Values': []}, {'Id': 'cpu_max', 'Values': [5.0]}, {'Id': 'net_avg', 'Values': [1.0]}]
        with patch.object(m.boto3, 'client', return_value=self._cw_client(results)):
            assert m.get_metrics('us-east-1', 'i-1')['avg_cpu'] is None

    def test_scopes_the_query_to_this_instance_id(self):
        client = self._cw_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.get_metrics('us-east-1', 'i-42')
        queries = client.get_metric_data.call_args.kwargs['MetricDataQueries']
        cpu_query = next(q for q in queries if q['Id'] == 'cpu_avg')
        assert cpu_query['MetricStat']['Metric']['Dimensions'] == [{'Name': 'InstanceId', 'Value': 'i-42'}]
