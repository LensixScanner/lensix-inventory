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
    'describe_transit_gateway_vpc_attachments': 'TransitGatewayVpcAttachments',
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


class TestGatherTagPassthrough:
    def test_every_tag_bearing_resource_type_passes_its_own_tags_through(self):
        # All nine resource types here read from EC2's own describe_*
        # responses, which all carry a plain 'Tags' list — one composite
        # test covering every add_resource() call site rather than nine
        # near-identical ones.
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        w = MagicMock()
        items = {
            'describe_vpcs':                     [{'VpcId': 'vpc-1', 'Tags': tags}],
            'describe_subnets':                  [{'SubnetId': 'subnet-1', 'Tags': tags}],
            'describe_route_tables':              [{'RouteTableId': 'rtb-1', 'Tags': tags}],
            'describe_nat_gateways':              [{'NatGatewayId': 'nat-1', 'Tags': tags}],
            'describe_internet_gateways':         [{'InternetGatewayId': 'igw-1', 'Tags': tags, 'Attachments': []}],
            'describe_network_acls':              [{'NetworkAclId': 'acl-1', 'Tags': tags}],
            'describe_vpc_endpoints':              [{'VpcEndpointId': 'vpce-1', 'Tags': tags}],
            'describe_vpc_peering_connections':   [{'VpcPeeringConnectionId': 'pcx-1', 'Tags': tags}],
            'describe_transit_gateways':          [{'TransitGatewayId': 'tgw-1', 'Tags': tags}],
        }
        with patch.object(m.boto3, 'client', return_value=_client(items, vpn_gateways=[{'VpnGatewayId': 'vgw-1', 'Tags': tags}])):
            m.gather('us-east-1', w)
        calls_by_type = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        for rtype in ('vpc', 'subnet', 'route_table', 'nat_gateway', 'internet_gateway',
                      'vpn_gateway', 'network_acl', 'vpc_endpoint', 'vpc_peering', 'transit_gateway'):
            assert calls_by_type[rtype].kwargs['tags'] == tags, f'{rtype} did not pass tags through'


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

    def test_vpc_attachments_are_fused_into_the_owning_transit_gateways_raw_record(self):
        w = MagicMock()
        tgw1 = {'TransitGatewayId': 'tgw-1'}
        tgw2 = {'TransitGatewayId': 'tgw-2'}
        att1 = {'TransitGatewayId': 'tgw-1', 'VpcId': 'vpc-1', 'SubnetIds': ['subnet-1']}
        att2 = {'TransitGatewayId': 'tgw-2', 'VpcId': 'vpc-2', 'SubnetIds': ['subnet-2']}
        client = _client(items={'describe_transit_gateways': [tgw1, tgw2], 'describe_transit_gateway_vpc_attachments': [att1, att2]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_id']: c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'transit_gateway'}
        assert calls['tgw-1'].kwargs['raw']['_VpcAttachments'] == [att1]
        assert calls['tgw-2'].kwargs['raw']['_VpcAttachments'] == [att2]

    def test_a_transit_gateway_with_no_attachments_gets_an_empty_list(self):
        w = MagicMock()
        tgw = {'TransitGatewayId': 'tgw-1'}
        client = _client(items={'describe_transit_gateways': [tgw]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['transit_gateway'].kwargs['raw']['_VpcAttachments'] == []

    def test_no_transit_gateways_skips_the_attachments_fetch_entirely(self):
        w = MagicMock()
        client = _client(items={'describe_transit_gateways': []})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert 'describe_transit_gateway_vpc_attachments' not in [c.args[0] for c in client.get_paginator.call_args_list]

    def test_a_vpc_attachments_fetch_error_is_captured_and_transit_gateways_still_gather(self):
        w = MagicMock()
        tgw = {'TransitGatewayId': 'tgw-1'}
        client = _client(items={'describe_transit_gateways': [tgw]}, raising_ops={'describe_transit_gateway_vpc_attachments'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'vpc (transit gateway vpc attachments)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['transit_gateway'].kwargs['raw']['_VpcAttachments'] == []


class TestGatherEdges:
    """Every relationship gather() itself now derives (via writer.add_edge())
    -- this used to live entirely in lensix-scanner-light's vpc_checks.py;
    see that module's own comment for why it moved here. w is a MagicMock
    throughout, so w.add_edge.call_args_list captures exactly what would
    have been passed to InventoryWriter.add_edge()."""

    def _edge_calls(self, w):
        return [c.kwargs for c in w.add_edge.call_args_list]

    def test_subnet_and_route_table_produce_vpc_edges(self):
        w = MagicMock()
        items = {
            'describe_subnets': [{'SubnetId': 'subnet-1', 'VpcId': 'vpc-1'}],
            'describe_route_tables': [{'RouteTableId': 'rtb-1', 'VpcId': 'vpc-1', 'Associations': []}],
        }
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        edges = self._edge_calls(w)
        assert {'from_type': 'subnet', 'from_id': 'subnet-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in edges
        assert {'from_type': 'route_table', 'from_id': 'rtb-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in edges

    def test_a_route_table_explicit_subnet_association_produces_an_edge(self):
        w = MagicMock()
        items = {'describe_route_tables': [{'RouteTableId': 'rtb-1', 'VpcId': 'vpc-1', 'Associations': [{'SubnetId': 'subnet-1'}, {'Main': True}]}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        assert {'from_type': 'subnet', 'from_id': 'subnet-1', 'to_type': 'route_table', 'to_id': 'rtb-1', 'relationship': 'uses_route_table'} in self._edge_calls(w)

    def test_nat_gateway_produces_vpc_and_subnet_edges(self):
        w = MagicMock()
        items = {'describe_nat_gateways': [{'NatGatewayId': 'nat-1', 'VpcId': 'vpc-1', 'SubnetId': 'subnet-1'}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        edges = self._edge_calls(w)
        assert {'from_type': 'nat_gateway', 'from_id': 'nat-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in edges
        assert {'from_type': 'nat_gateway', 'from_id': 'nat-1', 'to_type': 'subnet', 'to_id': 'subnet-1', 'relationship': 'in_subnet'} in edges

    def test_internet_gateway_produces_a_vpc_edge_from_its_attachment(self):
        w = MagicMock()
        items = {'describe_internet_gateways': [{'InternetGatewayId': 'igw-1', 'Attachments': [{'State': 'available', 'VpcId': 'vpc-1'}]}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        assert {'from_type': 'internet_gateway', 'from_id': 'igw-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in self._edge_calls(w)

    def test_unattached_internet_gateway_produces_no_edge(self):
        w = MagicMock()
        items = {'describe_internet_gateways': [{'InternetGatewayId': 'igw-1', 'Attachments': []}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_vpn_gateway_produces_a_vpc_edge_from_its_attachment(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_client(vpn_gateways=[{'VpnGatewayId': 'vgw-1', 'VpcAttachments': [{'State': 'attached', 'VpcId': 'vpc-1'}]}])):
            m.gather('us-east-1', w)
        assert {'from_type': 'vpn_gateway', 'from_id': 'vgw-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in self._edge_calls(w)

    def test_network_acl_produces_a_vpc_edge(self):
        w = MagicMock()
        items = {'describe_network_acls': [{'NetworkAclId': 'acl-1', 'VpcId': 'vpc-1'}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        assert {'from_type': 'network_acl', 'from_id': 'acl-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in self._edge_calls(w)

    def test_vpc_endpoint_produces_a_vpc_edge(self):
        w = MagicMock()
        items = {'describe_vpc_endpoints': [{'VpcEndpointId': 'vpce-1', 'VpcId': 'vpc-1'}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        assert {'from_type': 'vpc_endpoint', 'from_id': 'vpce-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'in_vpc'} in self._edge_calls(w)

    def test_peering_connection_produces_edges_to_both_vpcs(self):
        w = MagicMock()
        items = {'describe_vpc_peering_connections': [{
            'VpcPeeringConnectionId': 'pcx-1',
            'RequesterVpcInfo': {'VpcId': 'vpc-1'},
            'AccepterVpcInfo': {'VpcId': 'vpc-2'},
        }]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        edges = self._edge_calls(w)
        assert {'from_type': 'vpc_peering', 'from_id': 'pcx-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'peers_with'} in edges
        assert {'from_type': 'vpc_peering', 'from_id': 'pcx-1', 'to_type': 'vpc', 'to_id': 'vpc-2', 'relationship': 'peers_with'} in edges

    def test_peering_connection_with_only_a_requester_side_produces_one_edge(self):
        # The accepter VPC belongs to another AWS account Lensix doesn't
        # monitor -- AccepterVpcInfo can genuinely lack a VpcId Lensix has
        # visibility into, still only one real edge to produce.
        w = MagicMock()
        items = {'describe_vpc_peering_connections': [{
            'VpcPeeringConnectionId': 'pcx-1',
            'RequesterVpcInfo': {'VpcId': 'vpc-1'},
            'AccepterVpcInfo': {},
        }]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        assert self._edge_calls(w) == [{'from_type': 'vpc_peering', 'from_id': 'pcx-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'peers_with'}]

    def test_transit_gateway_attachment_produces_vpc_and_subnet_edges(self):
        w = MagicMock()
        items = {
            'describe_transit_gateways': [{'TransitGatewayId': 'tgw-1'}],
            'describe_transit_gateway_vpc_attachments': [{'TransitGatewayId': 'tgw-1', 'VpcId': 'vpc-1', 'SubnetIds': ['subnet-1', 'subnet-2']}],
        }
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        edges = self._edge_calls(w)
        assert {'from_type': 'transit_gateway', 'from_id': 'tgw-1', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'attached_to_vpc'} in edges
        assert {'from_type': 'transit_gateway', 'from_id': 'tgw-1', 'to_type': 'subnet', 'to_id': 'subnet-1', 'relationship': 'in_subnet'} in edges
        assert {'from_type': 'transit_gateway', 'from_id': 'tgw-1', 'to_type': 'subnet', 'to_id': 'subnet-2', 'relationship': 'in_subnet'} in edges

    def test_a_transit_gateway_with_no_attachments_produces_no_edges(self):
        w = MagicMock()
        items = {'describe_transit_gateways': [{'TransitGatewayId': 'tgw-1'}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_subnet_produces_no_edge(self):
        # add_resource() returning False (a real InventoryWriter's own
        # lensix-suppress=true handling) must suppress the matching edge
        # too, or a fully-suppressed resource -- one that's not supposed
        # to reach Lensix at all -- would still leak a reference via
        # resource_edges.
        w = MagicMock()
        w.add_resource.return_value = False
        items = {'describe_subnets': [{'SubnetId': 'subnet-1', 'VpcId': 'vpc-1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}]}
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_resources_with_no_vpc_id_produce_no_vpc_edges(self):
        w = MagicMock()
        items = {
            'describe_subnets': [{'SubnetId': 'subnet-1'}],
            'describe_route_tables': [{'RouteTableId': 'rtb-1'}],
            'describe_nat_gateways': [{'NatGatewayId': 'nat-1'}],
            'describe_network_acls': [{'NetworkAclId': 'acl-1'}],
            'describe_vpc_endpoints': [{'VpcEndpointId': 'vpce-1'}],
        }
        with patch.object(m.boto3, 'client', return_value=_client(items)):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()
