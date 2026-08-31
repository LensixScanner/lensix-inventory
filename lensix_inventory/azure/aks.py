"""Azure Kubernetes Service (AKS) gathering.

`managed_clusters.list()` already returns everything most evaluation needs
(enable_rbac, kubernetes_version, agent_pool_profiles, addon_profiles,
api_server_access_profile, network_profile, identity,
disk_encryption_set_id) — deprecated-version, RBAC/network-policy/BYOK, and
similar evaluation is left server-side.

Diagnostic settings need a per-cluster sub-call —
`diagnostic_settings.list(cluster.id)` — which is itself a plain list call,
so it's included too and merged into each cluster's raw record as
`_DiagnosticSettings`.
"""

from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.monitor import MonitorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_clusters(credential, subscription_id):
    aks_client = ContainerServiceClient(credential, subscription_id)
    return list(aks_client.managed_clusters.list())


def get_diagnostic_settings(credential, subscription_id, cluster_id):
    monitor_client = MonitorManagementClient(credential, subscription_id)
    try:
        return [_as_dict(s) for s in monitor_client.diagnostic_settings.list(cluster_id)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    for cluster in get_clusters(credential, subscription_id):
        raw = _as_dict(cluster)
        raw['_DiagnosticSettings'] = get_diagnostic_settings(credential, subscription_id, cluster.id)

        writer.add_resource(
            resource_type='kubernetes_cluster',
            region=cluster.location or 'global',
            resource_id=cluster.id,
            resource_name=cluster.name,
            scope_id=_resource_group(cluster.id),
            raw=raw,
            tags=raw.get('tags'),
        )
