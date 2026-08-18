"""Cloud DNS gathering — one raw record per managed zone.

The managed-zone list response already carries everything needed for
missing-DNSSEC and deprecated-algorithm evaluation (`visibility`,
`dnssecConfig.state`, `dnssecConfig.defaultKeySpecs`) in one call — no
per-zone fan-out required. That evaluation itself is left server-side.
"""

from googleapiclient import discovery


def get_managed_zones(dns, project_id):
    zones = []
    request = dns.managedZones().list(project=project_id)
    while request is not None:
        resp = request.execute()
        zones.extend(resp.get('managedZones', []))
        request = dns.managedZones().list_next(previous_request=request, previous_response=resp)
    return zones


def gather(project_id, credentials, writer):
    dns = discovery.build('dns', 'v1', credentials=credentials)

    try:
        zones = get_managed_zones(dns, project_id)
    except Exception as e:
        writer.add_error(region='global', source='dns_zone', message=e)
        return

    for zone in zones:
        name = zone.get('name', '')
        writer.add_resource(
            resource_type='dns_zone',
            region='global',
            resource_id=name,
            resource_name=name,
            raw=zone,
        )
