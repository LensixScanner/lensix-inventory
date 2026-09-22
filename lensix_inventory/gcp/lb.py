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

from . import _util, vpc


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

    # BackendService.network is documented only as "The URL of the
    # network..." (confirmed against the real discovery document schema)
    # — same ambiguous full/partial/bare-name convention as every other
    # Compute Engine reference field, resolved via vpc.py's own exported
    # name -> selfLink map for the same reason as compute.py's own
    # gather() (see its comment) — an accepted, disclosed duplicate of the
    # same list call vpc.py's own gather() already makes elsewhere in the
    # same scan, isolated so a resolution failure doesn't block backend
    # service gathering itself.
    try:
        network_id_by_name = vpc.get_network_selflink_by_name(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='lb_backend_service (network resolution)', message=e)
        network_id_by_name = {}

    try:
        for backend in get_backend_services(compute, project_id):
            name = backend.get('name', '')
            region = _region_from_self_link(backend.get('selfLink', ''))
            backend_id = backend.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='lb_backend_service',
                region=region,
                resource_id=backend_id,
                resource_name=name,
                scope_id=_util.extract_network_name(backend.get('network')),
                raw=backend,
            )
            if recorded:
                network_id = network_id_by_name.get(_util.extract_network_name(backend.get('network')))
                if network_id:
                    writer.add_edge(from_type='lb_backend_service', from_id=backend_id, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
    except Exception as e:
        writer.add_error(region='global', source='lb_backend_service', message=e)

    # ssl_policy is gathered before target_https_proxy (not its original
    # position, which was already this order) so this map is ready before
    # target_https_proxy needs it below.
    ssl_policy_id_by_name = {}
    try:
        for ssl_policy in get_ssl_policies(compute, project_id):
            name = ssl_policy.get('name', '')
            policy_id = ssl_policy.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='ssl_policy',
                region='global',
                resource_id=policy_id,
                resource_name=name,
                raw=ssl_policy,
            )
            if recorded:
                ssl_policy_id_by_name[name] = policy_id
    except Exception as e:
        writer.add_error(region='global', source='ssl_policy', message=e)

    try:
        for proxy in get_target_https_proxies(compute, project_id):
            name = proxy.get('name', '')
            region = _region_from_self_link(proxy.get('selfLink', ''))
            proxy_id = proxy.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='target_https_proxy',
                region=region,
                resource_id=proxy_id,
                resource_name=name,
                raw=proxy,
            )
            if recorded:
                # TargetHttpsProxy.sslPolicy is likewise only documented
                # as "URL of SslPolicy resource..." — same resolution
                # need as the network edge above, this time via a purely
                # local map (ssl_policy is gathered by this same function,
                # no cross-module fetch needed).
                policy_id = ssl_policy_id_by_name.get(_util.extract_resource_name(proxy.get('sslPolicy')))
                if policy_id:
                    writer.add_edge(from_type='target_https_proxy', from_id=proxy_id, to_type='ssl_policy', to_id=policy_id, relationship='uses_ssl_policy')
    except Exception as e:
        writer.add_error(region='global', source='target_https_proxy', message=e)
