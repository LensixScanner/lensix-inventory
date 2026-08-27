"""VPC networking gathering — VPCs, subnets, route tables, NAT/internet/VPN
gateways, network ACLs, VPC endpoints, peering connections, transit
gateways.

Cross-resource "is this VPC unused" style evaluation (unused VPC, single-
subnet, multi-AZ NAT, missing flow logs) needs VPCs correlated against
subnets/ENIs/NAT-gateways/flow-logs all at once — but that correlation only
matters for evaluating a finding, not for gathering. This tool just gathers
every resource type in the region independently (each subnet/route-table/
NAT-gateway/etc. already carries its own VpcId), and Lensix can recompute
any needed correlation server-side from the full set of uploaded resources
— no lookup-building helpers needed here at all.
"""

import boto3


def _tag_name(tags, fallback):
    return next((t['Value'] for t in (tags or []) if t['Key'] == 'Name'), fallback)


def get_vpcs(region):
    ec2 = boto3.client('ec2', region_name=region)
    vpcs = []
    for page in ec2.get_paginator('describe_vpcs').paginate():
        vpcs.extend(page['Vpcs'])
    return vpcs


def get_flow_logs(region):
    """Server-side filtered to VPC-scoped flow logs only — a FlowLog
    object has no client-inspectable 'ResourceType' field of its own (its
    ResourceId alone doesn't disambiguate a VPC id from a subnet/ENI id
    without parsing the prefix), so the resource-type filter has to be
    passed to the API call itself rather than applied after the fact."""
    ec2 = boto3.client('ec2', region_name=region)
    logs = []
    for page in ec2.get_paginator('describe_flow_logs').paginate(Filters=[{'Name': 'resource-type', 'Values': ['VPC']}]):
        logs.extend(page['FlowLogs'])
    return logs


def get_subnets(region):
    ec2 = boto3.client('ec2', region_name=region)
    subnets = []
    for page in ec2.get_paginator('describe_subnets').paginate():
        subnets.extend(page['Subnets'])
    return subnets


def get_route_tables(region):
    ec2 = boto3.client('ec2', region_name=region)
    tables = []
    for page in ec2.get_paginator('describe_route_tables').paginate():
        tables.extend(page['RouteTables'])
    return tables


def get_nat_gateways(region):
    ec2 = boto3.client('ec2', region_name=region)
    nats = []
    for page in ec2.get_paginator('describe_nat_gateways').paginate(Filters=[{'Name': 'state', 'Values': ['available']}]):
        nats.extend(page['NatGateways'])
    return nats


def get_internet_gateways(region):
    ec2 = boto3.client('ec2', region_name=region)
    igws = []
    for page in ec2.get_paginator('describe_internet_gateways').paginate():
        igws.extend(page['InternetGateways'])
    return igws


def get_vpn_gateways(region):
    ec2 = boto3.client('ec2', region_name=region)
    return ec2.describe_vpn_gateways()['VpnGateways']


def get_network_acls(region):
    ec2 = boto3.client('ec2', region_name=region)
    nacls = []
    for page in ec2.get_paginator('describe_network_acls').paginate():
        nacls.extend(page['NetworkAcls'])
    return nacls


def get_vpc_endpoints(region):
    ec2 = boto3.client('ec2', region_name=region)
    endpoints = []
    for page in ec2.get_paginator('describe_vpc_endpoints').paginate():
        endpoints.extend(page['VpcEndpoints'])
    return endpoints


def get_peering_connections(region):
    ec2 = boto3.client('ec2', region_name=region)
    conns = []
    for page in ec2.get_paginator('describe_vpc_peering_connections').paginate():
        conns.extend(page['VpcPeeringConnections'])
    return conns


def get_transit_gateways(region):
    ec2 = boto3.client('ec2', region_name=region)
    tgws = []
    for page in ec2.get_paginator('describe_transit_gateways').paginate():
        tgws.extend(page['TransitGateways'])
    return tgws


def gather(region, writer):
    # Ten independent describe calls — isolate each so one's failure
    # doesn't discard the others.
    try:
        flow_logs = get_flow_logs(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (flow logs)', message=e)
        flow_logs = []
    # get_flow_logs() above is already server-side filtered to VPC-scoped
    # entries only — every result here is a VPC flow log by construction.
    flow_logs_by_vpc = {}
    for fl in flow_logs:
        flow_logs_by_vpc.setdefault(fl['ResourceId'], []).append(fl)

    try:
        vpcs = get_vpcs(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (vpcs)', message=e)
        vpcs = []
    for vpc in vpcs:
        vpc_id = vpc['VpcId']
        raw = dict(vpc)
        raw['_FlowLogs'] = flow_logs_by_vpc.get(vpc_id, [])
        writer.add_resource(
            resource_type='vpc', region=region, resource_id=vpc_id,
            resource_name=_tag_name(vpc.get('Tags'), vpc_id), scope_id=vpc_id, raw=raw,
        )

    try:
        subnets = get_subnets(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (subnets)', message=e)
        subnets = []
    for s in subnets:
        writer.add_resource(
            resource_type='subnet', region=region, resource_id=s['SubnetId'],
            resource_name=_tag_name(s.get('Tags'), s['SubnetId']), scope_id=s.get('VpcId'), raw=s,
        )

    try:
        route_tables = get_route_tables(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (route tables)', message=e)
        route_tables = []
    for rt in route_tables:
        writer.add_resource(
            resource_type='route_table', region=region, resource_id=rt['RouteTableId'],
            resource_name=_tag_name(rt.get('Tags'), rt['RouteTableId']), scope_id=rt.get('VpcId'), raw=rt,
        )

    try:
        nat_gateways = get_nat_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (nat gateways)', message=e)
        nat_gateways = []
    for nat in nat_gateways:
        writer.add_resource(
            resource_type='nat_gateway', region=region, resource_id=nat['NatGatewayId'],
            resource_name=_tag_name(nat.get('Tags'), nat['NatGatewayId']), scope_id=nat.get('VpcId'), raw=nat,
        )

    try:
        igws = get_internet_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (internet gateways)', message=e)
        igws = []
    for igw in igws:
        vpc_id = next((a['VpcId'] for a in igw.get('Attachments', [])), None)
        writer.add_resource(
            resource_type='internet_gateway', region=region, resource_id=igw['InternetGatewayId'],
            resource_name=_tag_name(igw.get('Tags'), igw['InternetGatewayId']), scope_id=vpc_id, raw=igw,
        )

    try:
        vgws = get_vpn_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (vpn gateways)', message=e)
        vgws = []
    for vgw in vgws:
        vpc_id = next((a['VpcId'] for a in vgw.get('VpcAttachments', [])), None)
        writer.add_resource(
            resource_type='vpn_gateway', region=region, resource_id=vgw['VpnGatewayId'],
            resource_name=_tag_name(vgw.get('Tags'), vgw['VpnGatewayId']), scope_id=vpc_id, raw=vgw,
        )

    try:
        nacls = get_network_acls(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (network acls)', message=e)
        nacls = []
    for nacl in nacls:
        writer.add_resource(
            resource_type='network_acl', region=region, resource_id=nacl['NetworkAclId'],
            resource_name=_tag_name(nacl.get('Tags'), nacl['NetworkAclId']), scope_id=nacl.get('VpcId'), raw=nacl,
        )

    try:
        endpoints = get_vpc_endpoints(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (vpc endpoints)', message=e)
        endpoints = []
    for ep in endpoints:
        writer.add_resource(
            resource_type='vpc_endpoint', region=region, resource_id=ep['VpcEndpointId'],
            resource_name=ep.get('ServiceName', ep['VpcEndpointId']), scope_id=ep.get('VpcId'), raw=ep,
        )

    try:
        peering_conns = get_peering_connections(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (peering connections)', message=e)
        peering_conns = []
    for pc in peering_conns:
        vpc_id = pc.get('AccepterVpcInfo', {}).get('VpcId') or pc.get('RequesterVpcInfo', {}).get('VpcId')
        writer.add_resource(
            resource_type='vpc_peering', region=region, resource_id=pc['VpcPeeringConnectionId'],
            resource_name=_tag_name(pc.get('Tags'), pc['VpcPeeringConnectionId']), scope_id=vpc_id, raw=pc,
        )

    try:
        tgws = get_transit_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (transit gateways)', message=e)
        tgws = []
    for tgw in tgws:
        writer.add_resource(
            resource_type='transit_gateway', region=region, resource_id=tgw['TransitGatewayId'],
            resource_name=_tag_name(tgw.get('Tags'), tgw['TransitGatewayId']), raw=tgw,
        )
