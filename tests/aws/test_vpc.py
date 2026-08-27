"""Unit tests for lensix_inventory.aws.vpc — VPCs, subnets, route tables,
gateways, network ACLs, VPC endpoints, peering connections, and transit
gateways."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.vpc as m


_PAGINATED_OPS = {
    'describe_vpcs': 'Vpcs', 'describe_flow_logs': 'FlowLogs', 'describe_subnets': 'Subnets',
    'describe_route_tables': 'RouteTables', 'describe_nat_gateways': 'NatGateways',
    'describe_internet_gateways': 'InternetGateways', 'describe_network_acls': 'NetworkAcls',
    'describe_vpc_endpoints': 'VpcEndpoints', 'describe_vpc_peering_connections': 'VpcPeeringConnections',
    'describe_transit_gateways': 'TransitGateways',
}


def _client(items=None, raising_ops=None, vpn_gateways=None, vpn_gateways_raise=False):
    """items: {op_name: [list of raw items]}. raising_ops: set of op_names
    whose paginator.paginate() should raise instead."""
    items = items or {}
    raising_ops = raising_ops or set()
    client = MagicMock()

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name in raising_ops:
            p.paginate.side_effect = RuntimeError('boom')
        else:
            key = _PAGINATED_OPS[op_name]
            p.paginate.return_value = [{key: items.get(op_name, [])}]
        return p
    client.get_paginator.side_effect = _get_paginator

    if vpn_gateways_raise:
        client.describe_vpn_gateways.side_effect = RuntimeError('boom')
    else:
        client.describe_vpn_gateways.return_value = {'VpnGateways': vpn_gateways or []}
    return client


class TestTagName:
    def test_uses_the_name_tag(self):
        assert m._tag_name([{'Key': 'Name', 'Value': 'prod-vpc'}], 'vpc-1') == 'prod-vpc'

    def test_falls_back_without_a_name_tag(self):
        assert m._tag_name([], 'vpc-1') == 'vpc-1'


class TestGather:
    def test_adds_a_vpc_resource_with_its_own_flow_logs_merged_in(self):
        w = MagicMock()
        vpc = {'VpcId': 'vpc-1', 'Tags': [{'Key': 'Name', 'Value': 'prod'}]}
        flow_log = {'ResourceId': 'vpc-1', 'FlowLogId': 'fl-1'}
        client = _client(items={'describe_vpcs': [vpc], 'describe_flow_logs': [flow_log]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        vpc_call = calls['vpc']
        assert vpc_call.kwargs['resource_id'] == 'vpc-1'
        assert vpc_call.kwargs['scope_id'] == 'vpc-1'
        assert vpc_call.kwargs['raw']['_FlowLogs'] == [flow_log]

    def test_a_vpc_with_no_flow_logs_gets_an_empty_list(self):
        w = MagicMock()
        vpc = {'VpcId': 'vpc-1'}
        client = _client(items={'describe_vpcs': [vpc]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['vpc'].kwargs['raw']['_FlowLogs'] == []

    def test_adds_a_subnet_resource_scoped_to_its_vpc(self):
        w = MagicMock()
        subnet = {'SubnetId': 'subnet-1', 'VpcId': 'vpc-1', 'Tags': [{'Key': 'Name', 'Value': 'private-1a'}]}
        client = _client(items={'describe_subnets': [subnet]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['subnet'].kwargs['resource_name'] == 'private-1a'
        assert calls['subnet'].kwargs['scope_id'] == 'vpc-1'

    def test_adds_a_route_table_resource(self):
        w = MagicMock()
        rt = {'RouteTableId': 'rtb-1', 'VpcId': 'vpc-1'}
        client = _client(items={'describe_route_tables': [rt]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['route_table'].kwargs['resource_id'] == 'rtb-1'

    def test_adds_a_nat_gateway_resource(self):
        w = MagicMock()
        nat = {'NatGatewayId': 'nat-1', 'VpcId': 'vpc-1'}
        client = _client(items={'describe_nat_gateways': [nat]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['nat_gateway'].kwargs['resource_id'] == 'nat-1'

    def test_internet_gateway_scope_id_resolved_from_attachments(self):
        w = MagicMock()
        igw = {'InternetGatewayId': 'igw-1', 'Attachments': [{'VpcId': 'vpc-1'}]}
        client = _client(items={'describe_internet_gateways': [igw]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['internet_gateway'].kwargs['scope_id'] == 'vpc-1'

    def test_internet_gateway_with_no_attachments_has_no_scope_id(self):
        w = MagicMock()
        igw = {'InternetGatewayId': 'igw-1', 'Attachments': []}
        client = _client(items={'describe_internet_gateways': [igw]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['internet_gateway'].kwargs['scope_id'] is None

    def test_vpn_gateway_scope_id_resolved_from_vpc_attachments(self):
        w = MagicMock()
        vgw = {'VpnGatewayId': 'vgw-1', 'VpcAttachments': [{'VpcId': 'vpc-1'}]}
        client = _client(vpn_gateways=[vgw])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['vpn_gateway'].kwargs['scope_id'] == 'vpc-1'

    def test_a_vpn_gateways_failure_is_recorded_and_does_not_abort_the_others(self):
        w = MagicMock()
        vpc = {'VpcId': 'vpc-1'}
        client = _client(items={'describe_vpcs': [vpc]}, vpn_gateways_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'vpc (vpn gateways)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'vpc' in calls

    def test_adds_a_network_acl_resource(self):
        w = MagicMock()
        nacl = {'NetworkAclId': 'acl-1', 'VpcId': 'vpc-1'}
        client = _client(items={'describe_network_acls': [nacl]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['network_acl'].kwargs['resource_id'] == 'acl-1'

    def test_vpc_endpoint_name_falls_back_to_service_name(self):
        w = MagicMock()
        ep = {'VpcEndpointId': 'vpce-1', 'ServiceName': 'com.amazonaws.us-east-1.s3', 'VpcId': 'vpc-1'}
        client = _client(items={'describe_vpc_endpoints': [ep]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['vpc_endpoint'].kwargs['resource_name'] == 'com.amazonaws.us-east-1.s3'

    def test_peering_connection_scope_id_prefers_accepter_vpc(self):
        w = MagicMock()
        pc = {
            'VpcPeeringConnectionId': 'pcx-1',
            'AccepterVpcInfo': {'VpcId': 'vpc-accepter'},
            'RequesterVpcInfo': {'VpcId': 'vpc-requester'},
        }
        client = _client(items={'describe_vpc_peering_connections': [pc]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['vpc_peering'].kwargs['scope_id'] == 'vpc-accepter'

    def test_peering_connection_falls_back_to_requester_vpc(self):
        w = MagicMock()
        pc = {'VpcPeeringConnectionId': 'pcx-1', 'AccepterVpcInfo': {}, 'RequesterVpcInfo': {'VpcId': 'vpc-requester'}}
        client = _client(items={'describe_vpc_peering_connections': [pc]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['vpc_peering'].kwargs['scope_id'] == 'vpc-requester'

    def test_every_fetch_is_isolated_from_the_others(self):
        w = MagicMock()
        client = _client(raising_ops=set(_PAGINATED_OPS.keys()), vpn_gateways_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert sources == {
            'vpc (flow logs)', 'vpc (vpcs)', 'vpc (subnets)', 'vpc (route tables)',
            'vpc (nat gateways)', 'vpc (internet gateways)', 'vpc (vpn gateways)',
            'vpc (network acls)', 'vpc (vpc endpoints)', 'vpc (peering connections)',
            'vpc (transit gateways)',
        }
        w.add_resource.assert_not_called()

    def test_the_original_vpc_dict_is_not_mutated(self):
        w = MagicMock()
        vpc = {'VpcId': 'vpc-1'}
        client = _client(items={'describe_vpcs': [vpc]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_FlowLogs' not in vpc

    def test_adds_one_resource_per_transit_gateway(self):
        w = MagicMock()
        tgw = {'TransitGatewayId': 'tgw-1', 'Tags': [{'Key': 'Name', 'Value': 'my-tgw'}]}
        client = _client(items={'describe_transit_gateways': [tgw]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        tgw_call = calls['transit_gateway']
        assert tgw_call.kwargs['resource_id'] == 'tgw-1'
        assert tgw_call.kwargs['resource_name'] == 'my-tgw'

    def test_transit_gateway_falls_back_to_id_without_a_name_tag(self):
        w = MagicMock()
        tgw = {'TransitGatewayId': 'tgw-1'}
        client = _client(items={'describe_transit_gateways': [tgw]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['transit_gateway'].kwargs['resource_name'] == 'tgw-1'
