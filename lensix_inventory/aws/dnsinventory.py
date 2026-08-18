"""DNS record gathering — A/AAAA/CNAME record sets within each public
Route 53 hosted zone.

This module covers only DNS record sets within each hosted zone — plain
`resources`-shaped records, one per record set. The zones themselves are
NOT re-gathered here — `route53.py` already owns `route53_zone` (it needs
the zone list anyway for the apex-TXT/SPF check), so this module reuses
`route53.get_public_zones()` and only adds the DNS records within each
zone, linked back via `scope_id`.

Other DNS-adjacent identifiers are deliberately not duplicated here, since
each already appears on the resource type that owns it:
  - Elastic IPs — network.py.
  - ENIs — ec2.py.
  - CloudFront distributions + aliases — cloudfront.py (`DomainName` and
    `Aliases` are both present on each `cloudfront_distribution` record).
  - Classic/modern load balancer DNS names and RDS instance/cluster
    endpoints — no load-balancer or RDS gather module exists yet in this
    tool; these belong there when added, not duplicated here.
"""

import boto3

from .route53 import get_public_zones

_RELEVANT_RECORD_TYPES = {'A', 'AAAA', 'CNAME'}


def _normalize_hostname(value):
    return value.rstrip('.').lower().replace(r'\052', '*')


def get_record_sets(zone_id):
    r53 = boto3.client('route53')
    record_sets = []
    kwargs = {'HostedZoneId': zone_id}
    while True:
        resp = r53.list_resource_record_sets(**kwargs)
        record_sets.extend(resp['ResourceRecordSets'])
        if not resp.get('IsTruncated'):
            break
        kwargs = {
            'HostedZoneId': zone_id,
            'StartRecordName': resp['NextRecordName'],
            'StartRecordType': resp['NextRecordType'],
        }
        if resp.get('NextRecordIdentifier'):
            kwargs['StartRecordIdentifier'] = resp['NextRecordIdentifier']
    return record_sets


def gather(writer):
    for zone_id, _zone_name in get_public_zones():
        clean_id = zone_id.split('/')[-1]
        try:
            record_sets = get_record_sets(zone_id)
        except Exception as e:
            writer.add_error(region='global', source=f'route53_zone:{clean_id}', message=e)
            continue

        for rs in record_sets:
            if rs['Type'] not in _RELEVANT_RECORD_TYPES:
                continue
            name = _normalize_hostname(rs['Name'])
            resource_id = f"{clean_id}:{name}:{rs['Type']}"
            if rs.get('SetIdentifier'):
                resource_id += f":{rs['SetIdentifier']}"
            writer.add_resource(
                resource_type='route53_record',
                region='global',
                resource_id=resource_id,
                resource_name=name,
                scope_id=clean_id,
                raw=rs,
            )
