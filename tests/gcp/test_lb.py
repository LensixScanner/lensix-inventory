"""Unit tests for lb.py — backend services, SSL policies, and target HTTPS
proxies.

No test file existed for this module before resource_edges support was
added (previously exercised only indirectly through
gcp/scanmodules/test_lb_checks.py in lensix-scanner-light) — this covers
gather()'s own resource/edge wiring directly. No tags= anywhere in this
module: none of BackendService, SslPolicy, or TargetHttpsProxy have a
`labels` field in the Compute Engine v1 API at all — confirmed against the
real discovery document schema — a genuine architectural N/A, same class
as kms.py's own KeyRing.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.lb as m


def _backend(*, name='my-backend', self_link='https://compute.../backendServices/my-backend', network=None):
    d = {'name': name, 'selfLink': self_link}
    if network is not None:
        d['network'] = network
    return d


def _ssl_policy(*, name='my-ssl-policy', self_link='https://compute.../sslPolicies/my-ssl-policy'):
    return {'name': name, 'selfLink': self_link}


def _proxy(*, name='my-proxy', self_link='https://compute.../targetHttpsProxies/my-proxy', ssl_policy=None):
    d = {'name': name, 'selfLink': self_link}
    if ssl_policy is not None:
        d['sslPolicy'] = ssl_policy
    return d


def _compute_client(backends=None, ssl_policies=None, proxies=None, networks=None):
    compute = MagicMock()

    backend_req = MagicMock()
    backend_req.execute.return_value = {'items': {'global': {'backendServices': backends or []}}}
    compute.backendServices.return_value.aggregatedList.return_value = backend_req
    compute.backendServices.return_value.aggregatedList_next.return_value = None

    ssl_req = MagicMock()
    ssl_req.execute.return_value = {'items': ssl_policies or []}
    compute.sslPolicies.return_value.list.return_value = ssl_req
    compute.sslPolicies.return_value.list_next.return_value = None

    proxy_req = MagicMock()
    proxy_req.execute.return_value = {'items': {'global': {'targetHttpsProxies': proxies or []}}}
    compute.targetHttpsProxies.return_value.aggregatedList.return_value = proxy_req
    compute.targetHttpsProxies.return_value.aggregatedList_next.return_value = None

    # gather() also resolves each backend service's network reference via
    # vpc.py's own exported map (see gather()'s own comment) — empty by
    # default so tests not exercising those edges don't need to know about
    # this at all, and so the real pagination loop doesn't spin forever
    # against an unconfigured MagicMock's own always-truthy list_next()
    # return.
    net_req = MagicMock()
    net_req.execute.return_value = {'items': networks or []}
    compute.networks.return_value.list.return_value = net_req
    compute.networks.return_value.list_next.return_value = None

    return compute


class TestGather:
    def test_adds_one_resource_per_type(self):
        compute = _compute_client(backends=[_backend()], ssl_policies=[_ssl_policy()], proxies=[_proxy()])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert resource_types == ['lb_backend_service', 'ssl_policy', 'target_https_proxy']

    def test_a_backend_services_failure_does_not_prevent_the_other_types(self):
        compute = _compute_client(ssl_policies=[_ssl_policy()])
        compute.backendServices.return_value.aggregatedList.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'lb_backend_service' for c in writer.add_error.call_args_list)
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert resource_types == ['ssl_policy']

    def test_nothing_adds_nothing(self):
        compute = _compute_client()
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_backend_service_produces_a_vpc_network_edge(self):
        network = {'name': 'default', 'selfLink': 'https://compute.../networks/default'}
        backend = _backend(network='https://compute.../networks/default')
        compute = _compute_client(backends=[backend], networks=[network])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='lb_backend_service', from_id=backend['selfLink'],
            to_type='vpc_network', to_id=network['selfLink'], relationship='in_vpc_network',
        )

    def test_a_backend_service_with_no_network_produces_no_edge(self):
        compute = _compute_client(backends=[_backend()])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_target_https_proxy_produces_a_uses_ssl_policy_edge(self):
        ssl_policy = _ssl_policy(name='my-ssl-policy')
        proxy = _proxy(ssl_policy='my-ssl-policy')
        compute = _compute_client(ssl_policies=[ssl_policy], proxies=[proxy])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='target_https_proxy', from_id=proxy['selfLink'],
            to_type='ssl_policy', to_id=ssl_policy['selfLink'], relationship='uses_ssl_policy',
        )

    def test_a_target_https_proxy_with_no_ssl_policy_produces_no_edge(self):
        proxy = _proxy()
        compute = _compute_client(proxies=[proxy])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_proxy_referencing_an_unresolvable_ssl_policy_produces_no_edge(self):
        proxy = _proxy(ssl_policy='unknown-policy')
        compute = _compute_client(ssl_policies=[_ssl_policy()], proxies=[proxy])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_an_unresolvable_network_produces_no_edge(self):
        backend = _backend(network='https://compute.../networks/unknown')
        compute = _compute_client(backends=[backend], networks=[{'name': 'default', 'selfLink': 'https://compute.../networks/default'}])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_fully_suppressed_backend_service_is_not_added_to_the_map(self):
        # BackendService has no tags/labels concept at all (see module
        # docstring), so full suppression can never actually trigger here
        # in practice -- this exercises the guard defensively anyway, same
        # discipline as every other migrated module this effort.
        backend = _backend(network='https://compute.../networks/default')
        network = {'name': 'default', 'selfLink': 'https://compute.../networks/default'}
        compute = _compute_client(backends=[backend], networks=[network])
        writer = MagicMock()
        writer.add_resource.return_value = False
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_network_resolution_failure_produces_no_vpc_network_edge(self):
        backend = _backend(network='https://compute.../networks/default')
        compute = _compute_client(backends=[backend])
        compute.networks.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'lb_backend_service (network resolution)' for c in writer.add_error.call_args_list)
        writer.add_edge.assert_not_called()
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert 'lb_backend_service' in resource_types
