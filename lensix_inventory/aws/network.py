"""Network gathering — Elastic IPs.

describe_addresses returns every Elastic IP in the region (attached or
not) in one shot. "Unattached == unused" is evaluation, not gathering:
every EIP, attached or not, is uploaded here and Lensix determines "unused"
server-side from the presence/absence of AssociationId.
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
        writer.add_resource(
            resource_type='elastic_ip',
            region=region,
            resource_id=alloc,
            resource_name=ip,
            raw=eip,
        )
