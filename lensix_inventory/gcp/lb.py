"""Load balancer gathering — backend services, SSL policies, target HTTPS
proxies.

Each of the three resource types has its own aggregatedList/list call that
already returns everything needed for evaluation in one shot (logConfig,
enableCDN, securityPolicy, protocol on backend services; minTlsVersion/
profile on SSL policies; sslPolicy reference on target HTTPS proxies) — no
fan-out sub-API calls needed here, unlike compute.py/storage.py. Missing
logging, missing CDN, missing Cloud Armor policy, plain HTTP, weak TLS/
cipher profile, and missing custom SSL policy evaluation is left
server-side.
"""

from googleapiclient import discovery

from . import _util


def _region_from_self_link(self_link):
    if '/regions/' in (self_link or ''):
        return self_link.split('/regions/')[1].split('/')[0]
    return 'global'


def get_backend_services(compute, project_id):
    backends = []
    request = compute.backendServices().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for scope_data in resp.get('items', {}).values():
            backends.extend(scope_data.get('backendServices', []))
        request = compute.backendServices().aggregatedList_next(previous_request=request, previous_response=resp)
    return backends


def get_ssl_policies(compute, project_id):
    policies = []
    request = compute.sslPolicies().list(project=project_id)
    while request is not None:
        resp = request.execute()
        policies.extend(resp.get('items', []))
        request = compute.sslPolicies().list_next(previous_request=request, previous_response=resp)
    return policies


def get_target_https_proxies(compute, project_id):
    proxies = []
    request = compute.targetHttpsProxies().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for scope_data in resp.get('items', {}).values():
            proxies.extend(scope_data.get('targetHttpsProxies', []))
        request = compute.targetHttpsProxies().aggregatedList_next(previous_request=request, previous_response=resp)
    return proxies


def gather(project_id, credentials, writer):
    # No tags= anywhere in this module: none of BackendService, SslPolicy,
    # or TargetHttpsProxy have a `labels` field in the Compute Engine v1
    # API at all — confirmed against the real discovery document schema
    # (same check that caught vpc.py's own Firewall/Network/Subnetwork
    # mistake — see docs/tag-suppressions.md), not assumed. A genuine
    # architectural N/A, same class as kms.py's own KeyRing.
    compute = discovery.build('compute', 'v1', credentials=credentials)

    try:
        for backend in get_backend_services(compute, project_id):
            name = backend.get('name', '')
            region = _region_from_self_link(backend.get('selfLink', ''))
            writer.add_resource(
                resource_type='lb_backend_service',
                region=region,
                resource_id=backend.get('selfLink', name),
                resource_name=name,
                scope_id=_util.extract_network_name(backend.get('network')),
                raw=backend,
            )
    except Exception as e:
        writer.add_error(region='global', source='lb_backend_service', message=e)

    try:
        for ssl_policy in get_ssl_policies(compute, project_id):
            name = ssl_policy.get('name', '')
            writer.add_resource(
                resource_type='ssl_policy',
                region='global',
                resource_id=ssl_policy.get('selfLink', name),
                resource_name=name,
                raw=ssl_policy,
            )
    except Exception as e:
        writer.add_error(region='global', source='ssl_policy', message=e)

    try:
        for proxy in get_target_https_proxies(compute, project_id):
            name = proxy.get('name', '')
            region = _region_from_self_link(proxy.get('selfLink', ''))
            writer.add_resource(
                resource_type='target_https_proxy',
                region=region,
                resource_id=proxy.get('selfLink', name),
                resource_name=name,
                raw=proxy,
            )
    except Exception as e:
        writer.add_error(region='global', source='target_https_proxy', message=e)
