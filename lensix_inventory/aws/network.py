"""Network gathering — Elastic IPs.

describe_addresses returns every Elastic IP in the region (attached or
not) in one shot. "Unattached == unused" is evaluation, not gathering:
every EIP, attached or not, is uploaded here and Lensix determines "unused"
server-side from the presence/absence of AssociationId.

Tags are already inline (EC2-family {'Key','Value'} list) — passed
straight through to add_resource() for tag-based suppression.
"""

import boto3


def get_eips(region):
    ec2 = boto3.client('ec2', region_name=region)
    resp = ec2.describe_addresses()
    return resp['Addresses']


def gather(region, writer):
    for eip in get_eips(region):
        ip = eip.get('PublicIp', '')
        alloc = eip.get('AllocationId', ip)
        recorded = writer.add_resource(
            resource_type='elastic_ip',
            region=region,
            resource_id=alloc,
            resource_name=ip,
            raw=eip,
            tags=eip.get('Tags'),
        )
        if not recorded:
            continue
        # An instance-associated EIP's NetworkInterfaceId is just that
        # instance's own primary ENI -- prefer the direct ec2_instance edge
        # over a redundant ENI hop in that case, only falling back to the
        # ENI edge for addresses attached directly to an interface with no
        # owning instance (e.g. a NAT gateway's EIP).
        if eip.get('InstanceId'):
            writer.add_edge(from_type='elastic_ip', from_id=alloc, to_type='ec2_instance', to_id=eip['InstanceId'], relationship='associated_with')
        elif eip.get('NetworkInterfaceId'):
            writer.add_edge(from_type='elastic_ip', from_id=alloc, to_type='elastic_network_interface', to_id=eip['NetworkInterfaceId'], relationship='associated_with')
        if eip.get('SubnetId'):
            writer.add_edge(from_type='elastic_ip', from_id=alloc, to_type='subnet', to_id=eip['SubnetId'], relationship='in_subnet')
