"""Route 53 gathering — registered domains and public hosted zones.

Both are global (route53domains is us-east-1-only; route53 hosted zones are
also a global resource), so this module never loops regions, mirroring
s3.py's `gather(writer, ...)` pattern instead of `gather(region, writer)`.

Domains: list_domains + get_domain_detail fused per domain into a
`route53_domain` record.

Zones: list_hosted_zones (public zones only) + the apex TXT record set
(needed for SPF evaluation) folded into each zone's raw record as
`_ApexTxtRecordSets` — the raw record data, not a computed has-SPF boolean
— matching s3.py's fused fan-out pattern, into a `route53_zone` record.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


# --- Domains ---

def get_domains():
    client = boto3.client('route53domains', region_name='us-east-1', config=_BOTO_CFG)
    domains = []
    kwargs = {}
    while True:
        resp = client.list_domains(**kwargs)
        domains.extend(d['DomainName'] for d in resp.get('Domains', []))
        token = resp.get('NextPageMarker')
        if not token:
            break
        kwargs['Marker'] = token
    return domains


def get_domain_detail(domain_name):
    client = boto3.client('route53domains', region_name='us-east-1', config=_BOTO_CFG)
    return client.get_domain_detail(DomainName=domain_name)


# --- Hosted zones ---

def get_public_zones():
    """Return list of (zone_id, zone_name) for all public hosted zones."""
    client = boto3.client('route53', config=_BOTO_CFG)
    zones = []
    kwargs = {}
    while True:
        resp = client.list_hosted_zones(**kwargs)
        for z in resp['HostedZones']:
            if not z['Config']['PrivateZone']:
                zones.append((z['Id'], z['Name']))
        marker = resp.get('NextMarker')
        if not resp.get('IsTruncated') or not marker:
            break
        kwargs['Marker'] = marker
    return zones


def get_apex_txt_records(zone_id, apex_name):
    """Returns the raw TXT record sets at the apex, not a computed has-SPF
    boolean."""
    client = boto3.client('route53', config=_BOTO_CFG)
    try:
        resp = client.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=apex_name,
            StartRecordType='TXT',
            MaxItems='10',
        )
        return [
            rrs for rrs in resp.get('ResourceRecordSets', [])
            if rrs['Type'] == 'TXT' and rrs['Name'].rstrip('.') == apex_name.rstrip('.')
        ]
    except Exception:
        return []


def gather(writer):
    # Domains (route53domains) and hosted zones (route53) are independent
    # services/fetches — isolate them so a failure fetching one doesn't
    # prevent the other from being gathered.
    try:
        for domain in get_domains():
            try:
                detail = get_domain_detail(domain)
            except Exception as e:
                writer.add_error(region='global', source=f'route53_domain:{domain}', message=e)
                continue
            writer.add_resource(
                resource_type='route53_domain',
                region='global',
                resource_id=domain,
                resource_name=domain,
                raw=detail,
            )
    except Exception as e:
        writer.add_error(region='global', source='route53 (domains)', message=e)

    try:
        for zone_id, zone_name in get_public_zones():
            # zone_id from list_hosted_zones is the full "/hostedzone/Z123..."
            # path; use the bare id (matching the AWS console/CLI convention)
            # as the resource_id — dnsinventory.py's route53_record entries
            # link back to a zone via this same stripped id in their scope_id.
            clean_id = zone_id.split('/')[-1]
            zone_name_clean = zone_name.rstrip('.')
            raw = {'Id': zone_id, 'Name': zone_name}
            raw['_ApexTxtRecordSets'] = get_apex_txt_records(zone_id, zone_name)
            writer.add_resource(
                resource_type='route53_zone',
                region='global',
                resource_id=clean_id,
                resource_name=zone_name_clean,
                raw=raw,
            )
    except Exception as e:
        writer.add_error(region='global', source='route53 (hosted zones)', message=e)
