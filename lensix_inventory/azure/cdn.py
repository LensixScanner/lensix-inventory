"""Azure CDN gathering — profiles, with each profile's endpoints merged in.

Like AWS's S3 module, evaluating CDN needs child resources (endpoints) that
aren't their own top-level resource type — only `cdn_profile` is a
persisted resource here — so endpoints are fetched per-profile via
`endpoints.list_by_profile` purely to support HTTP-enabled/logging
evaluation. One merged raw record per profile, with its endpoints (each
including a diagnostic-settings sub-fetch, itself a plain list call)
nested inside.
"""

from azure.mgmt.cdn import CdnManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_profiles(credential, subscription_id):
    cdn = CdnManagementClient(credential, subscription_id)
    return list(cdn.profiles.list())


def get_endpoints(credential, subscription_id, resource_group, profile_name):
    cdn = CdnManagementClient(credential, subscription_id)
    return list(cdn.endpoints.list_by_profile(resource_group, profile_name))


def get_diagnostic_settings(credential, subscription_id, resource_uri):
    monitor = MonitorManagementClient(credential, subscription_id)
    try:
        return [_as_dict(s) for s in monitor.diagnostic_settings.list(resource_uri=resource_uri)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    for profile in get_profiles(credential, subscription_id):
        rg = _resource_group(profile.id)
        raw = _as_dict(profile)

        endpoints_raw = []
        if rg:
            for endpoint in get_endpoints(credential, subscription_id, rg, profile.name):
                endpoint_raw = _as_dict(endpoint)
                endpoint_raw['_DiagnosticSettings'] = get_diagnostic_settings(
                    credential, subscription_id, endpoint.id
                )
                endpoints_raw.append(endpoint_raw)
        raw['_Endpoints'] = endpoints_raw

        writer.add_resource(
            resource_type='cdn_profile',
            region=profile.location or 'global',
            resource_id=profile.id,
            resource_name=profile.name,
            scope_id=rg,
            raw=raw,
        )
