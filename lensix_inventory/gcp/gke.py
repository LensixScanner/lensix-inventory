"""GKE gathering — clusters and their node pools.

A single `projects.locations.clusters.list` call with a wildcard location
(`locations/-`) already returns each cluster's full config, including its
nested `nodePools` array — no per-pool fan-out call needed. Legacy ABAC,
basic auth, master authorized networks, private endpoint/nodes, network
policy, web dashboard, Shielded Nodes, database encryption, logging/
monitoring service, IP aliasing, alpha features, pod security policy —
and, per node pool, default service account, COS image, secure boot/
integrity monitoring, node disk CMEK, auto-repair/auto-upgrade, legacy
metadata endpoints, workload metadata mode — evaluation is left
server-side.

Node pools are gathered as their own `gke_node_pool` resource records
(resource_id `<cluster_name>/<pool_name>`) rather than only nested inside
the cluster's raw record, since several checks apply per-pool.
"""

from googleapiclient import discovery

from . import _util


def get_clusters(container, project_id):
    resp = container.projects().locations().clusters().list(
        parent=f'projects/{project_id}/locations/-'
    ).execute()
    return resp.get('clusters', [])


def gather(project_id, credentials, writer):
    container = discovery.build('container', 'v1', credentials=credentials)

    try:
        clusters = get_clusters(container, project_id)
    except Exception as e:
        writer.add_error(region='global', source='gke_cluster', message=e)
        return

    for cluster in clusters:
        cluster_name = cluster.get('name', '')
        location = cluster.get('location', 'global')

        writer.add_resource(
            resource_type='gke_cluster',
            region=location,
            resource_id=cluster_name,
            resource_name=cluster_name,
            scope_id=_util.extract_network_name(cluster.get('network')),
            raw=cluster,
            # GKE's tags-equivalent field is resourceLabels, not a
            # top-level `labels` key — same userLabels-style naming quirk
            # as Cloud SQL/Cloud Monitoring (see sql.py/logging.py).
            tags=cluster.get('resourceLabels'),
        )

        for pool in cluster.get('nodePools', []):
            pool_name = pool.get('name', '')
            full_pool_name = f'{cluster_name}/{pool_name}'
            # No tags= here: a NodePool has no resource-level labels field
            # of its own — its config.labels are Kubernetes node labels
            # applied to the underlying VMs for workload scheduling, a
            # different concept from the GCP resource-tagging convention
            # this tool otherwise relies on (same distinction as GCE
            # instance metadata vs. resource labels) — a genuine
            # architectural N/A, not an oversight.
            writer.add_resource(
                resource_type='gke_node_pool',
                region=location,
                resource_id=full_pool_name,
                resource_name=full_pool_name,
                scope_id=_util.extract_network_name(cluster.get('network')),
                raw=pool,
            )
