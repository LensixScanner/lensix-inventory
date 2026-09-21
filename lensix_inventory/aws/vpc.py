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


def get_transit_gateway_vpc_attachments(region):
    """Region-wide, not per-TGW -- one paginated call answers it for every
    transit gateway at once. Each attachment's own VpcId/SubnetIds are
    fused into its owning TGW's raw record as `_VpcAttachments` (see
    gather()), same pattern as redshift.py's `_LoggingStatus`."""
    ec2 = boto3.client('ec2', region_name=region)
    attachments = []
    for page in ec2.get_paginator('describe_transit_gateway_vpc_attachments').paginate():
        attachments.extend(page.get('TransitGatewayVpcAttachments', []))
    return attachments


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
            tags=vpc.get('Tags'),
        )

    try:
        subnets = get_subnets(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (subnets)', message=e)
        subnets = []
    for s in subnets:
        recorded = writer.add_resource(
            resource_type='subnet', region=region, resource_id=s['SubnetId'],
            resource_name=_tag_name(s.get('Tags'), s['SubnetId']), scope_id=s.get('VpcId'), raw=s,
            tags=s.get('Tags'),
        )
        if recorded and s.get('VpcId'):
            writer.add_edge(from_type='subnet', from_id=s['SubnetId'], to_type='vpc', to_id=s['VpcId'], relationship='in_vpc')

    try:
        route_tables = get_route_tables(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (route tables)', message=e)
        route_tables = []
    for rt in route_tables:
        rt_id = rt['RouteTableId']
        recorded = writer.add_resource(
            resource_type='route_table', region=region, resource_id=rt_id,
            resource_name=_tag_name(rt.get('Tags'), rt_id), scope_id=rt.get('VpcId'), raw=rt,
            tags=rt.get('Tags'),
        )
        if not recorded:
            continue
        if rt.get('VpcId'):
            writer.add_edge(from_type='route_table', from_id=rt_id, to_type='vpc', to_id=rt['VpcId'], relationship='in_vpc')
        # Explicit subnet associations only -- the main/default
        # association (no SubnetId) implicitly covers every subnet in the
        # VPC that has no explicit association of its own, which isn't
        # representable as a single edge.
        for assoc in rt.get('Associations', []):
            if assoc.get('SubnetId'):
                writer.add_edge(from_type='subnet', from_id=assoc['SubnetId'], to_type='route_table', to_id=rt_id, relationship='uses_route_table')

    try:
        nat_gateways = get_nat_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (nat gateways)', message=e)
        nat_gateways = []
    for nat in nat_gateways:
        nat_id = nat['NatGatewayId']
        recorded = writer.add_resource(
            resource_type='nat_gateway', region=region, resource_id=nat_id,
            resource_name=_tag_name(nat.get('Tags'), nat_id), scope_id=nat.get('VpcId'), raw=nat,
            tags=nat.get('Tags'),
        )
        if not recorded:
            continue
        if nat.get('VpcId'):
            writer.add_edge(from_type='nat_gateway', from_id=nat_id, to_type='vpc', to_id=nat['VpcId'], relationship='in_vpc')
        if nat.get('SubnetId'):
            writer.add_edge(from_type='nat_gateway', from_id=nat_id, to_type='subnet', to_id=nat['SubnetId'], relationship='in_subnet')

    try:
        igws = get_internet_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (internet gateways)', message=e)
        igws = []
    for igw in igws:
        igw_id = igw['InternetGatewayId']
        vpc_id = next((a['VpcId'] for a in igw.get('Attachments', [])), None)
        recorded = writer.add_resource(
            resource_type='internet_gateway', region=region, resource_id=igw_id,
            resource_name=_tag_name(igw.get('Tags'), igw_id), scope_id=vpc_id, raw=igw,
            tags=igw.get('Tags'),
        )
        if recorded and vpc_id:
            writer.add_edge(from_type='internet_gateway', from_id=igw_id, to_type='vpc', to_id=vpc_id, relationship='in_vpc')

    try:
        vgws = get_vpn_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (vpn gateways)', message=e)
        vgws = []
    for vgw in vgws:
        vgw_id = vgw['VpnGatewayId']
        vpc_id = next((a['VpcId'] for a in vgw.get('VpcAttachments', [])), None)
        recorded = writer.add_resource(
            resource_type='vpn_gateway', region=region, resource_id=vgw_id,
            resource_name=_tag_name(vgw.get('Tags'), vgw_id), scope_id=vpc_id, raw=vgw,
            tags=vgw.get('Tags'),
        )
        if recorded and vpc_id:
            writer.add_edge(from_type='vpn_gateway', from_id=vgw_id, to_type='vpc', to_id=vpc_id, relationship='in_vpc')

    try:
        nacls = get_network_acls(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (network acls)', message=e)
        nacls = []
    for nacl in nacls:
        nacl_id = nacl['NetworkAclId']
        recorded = writer.add_resource(
            resource_type='network_acl', region=region, resource_id=nacl_id,
            resource_name=_tag_name(nacl.get('Tags'), nacl_id), scope_id=nacl.get('VpcId'), raw=nacl,
            tags=nacl.get('Tags'),
        )
        if recorded and nacl.get('VpcId'):
            writer.add_edge(from_type='network_acl', from_id=nacl_id, to_type='vpc', to_id=nacl['VpcId'], relationship='in_vpc')

    try:
        endpoints = get_vpc_endpoints(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (vpc endpoints)', message=e)
        endpoints = []
    for ep in endpoints:
        ep_id = ep['VpcEndpointId']
        recorded = writer.add_resource(
            resource_type='vpc_endpoint', region=region, resource_id=ep_id,
            resource_name=ep.get('ServiceName', ep_id), scope_id=ep.get('VpcId'), raw=ep,
            tags=ep.get('Tags'),
        )
        if recorded and ep.get('VpcId'):
            writer.add_edge(from_type='vpc_endpoint', from_id=ep_id, to_type='vpc', to_id=ep['VpcId'], relationship='in_vpc')

    try:
        peering_conns = get_peering_connections(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (peering connections)', message=e)
        peering_conns = []
    for pc in peering_conns:
        pc_id = pc['VpcPeeringConnectionId']
        requester_vpc = pc.get('RequesterVpcInfo', {}).get('VpcId')
        accepter_vpc = pc.get('AccepterVpcInfo', {}).get('VpcId')
        vpc_id = accepter_vpc or requester_vpc
        recorded = writer.add_resource(
            resource_type='vpc_peering', region=region, resource_id=pc_id,
            resource_name=_tag_name(pc.get('Tags'), pc_id), scope_id=vpc_id, raw=pc,
            tags=pc.get('Tags'),
        )
        if not recorded:
            continue
        # Both sides are recorded even though the accepter VPC may belong
        # to another AWS account entirely -- lensix-web-light's own
        # resolve.ts drops this edge later if that VPC never shows up as
        # a resource (i.e. it's not an account Lensix monitors).
        if requester_vpc:
            writer.add_edge(from_type='vpc_peering', from_id=pc_id, to_type='vpc', to_id=requester_vpc, relationship='peers_with')
        if accepter_vpc:
            writer.add_edge(from_type='vpc_peering', from_id=pc_id, to_type='vpc', to_id=accepter_vpc, relationship='peers_with')

    try:
        tgws = get_transit_gateways(region)
    except Exception as e:
        writer.add_error(region=region, source='vpc (transit gateways)', message=e)
        tgws = []

    attachments_by_tgw = {}
    if tgws:
        try:
            for att in get_transit_gateway_vpc_attachments(region):
                attachments_by_tgw.setdefault(att.get('TransitGatewayId'), []).append(att)
        except Exception as e:
            writer.add_error(region=region, source='vpc (transit gateway vpc attachments)', message=e)

    for tgw in tgws:
        tgw_id = tgw['TransitGatewayId']
        raw = dict(tgw)
        attachments = attachments_by_tgw.get(tgw_id, [])
        raw['_VpcAttachments'] = attachments
        recorded = writer.add_resource(
            resource_type='transit_gateway', region=region, resource_id=tgw_id,
            resource_name=_tag_name(tgw.get('Tags'), tgw_id), raw=raw,
            tags=tgw.get('Tags'),
        )
        if not recorded:
            continue
        for att in attachments:
            att_vpc_id = att.get('VpcId')
            if not att_vpc_id:
                continue
            writer.add_edge(from_type='transit_gateway', from_id=tgw_id, to_type='vpc', to_id=att_vpc_id, relationship='attached_to_vpc')
            for subnet_id in att.get('SubnetIds', []):
                writer.add_edge(from_type='transit_gateway', from_id=tgw_id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
