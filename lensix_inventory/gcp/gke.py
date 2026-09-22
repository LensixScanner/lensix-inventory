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

from . import _util, vpc


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

    # Cluster.network/subnetwork are documented as bare NAMES (confirmed
    # against the real discovery document schema), unlike Compute Engine's
    # own network-reference fields, which may come back as a full/partial
    # URL — but resolving through vpc.py's own name -> selfLink maps is
    # still required either way, since a vpc_network/subnet's own
    # resource_id is always the full selfLink, never the bare name a
    # Cluster carries. Built once per gather() call (an accepted,
    # disclosed duplicate of the same two list calls vpc.py's own gather()
    # already makes elsewhere in the same scan — see compute.py's own
    # identical comment), isolated so a resolution failure doesn't block
    # cluster gathering itself.
    compute = discovery.build('compute', 'v1', credentials=credentials)
    try:
        network_id_by_name = vpc.get_network_selflink_by_name(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='gke_cluster (network resolution)', message=e)
        network_id_by_name = {}
    try:
        subnet_id_by_key = vpc.get_subnet_selflink_by_key(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='gke_cluster (subnet resolution)', message=e)
        subnet_id_by_key = {}

    for cluster in clusters:
        cluster_name = cluster.get('name', '')
        location = cluster.get('location', 'global')

        recorded = writer.add_resource(
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
        if recorded:
            network_id = network_id_by_name.get(_util.extract_network_name(cluster.get('network')))
            if network_id:
                writer.add_edge(from_type='gke_cluster', from_id=cluster_name, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
            subnet_name = cluster.get('subnetwork')
            if subnet_name:
                subnet_id = subnet_id_by_key.get((_util.normalize_location_to_region(location), subnet_name))
                if subnet_id:
                    writer.add_edge(from_type='gke_cluster', from_id=cluster_name, to_type='subnet', to_id=subnet_id, relationship='in_subnet')

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
            pool_recorded = writer.add_resource(
                resource_type='gke_node_pool',
                region=location,
                resource_id=full_pool_name,
                resource_name=full_pool_name,
                scope_id=_util.extract_network_name(cluster.get('network')),
                raw=pool,
            )
            if pool_recorded and recorded:
                writer.add_edge(from_type='gke_node_pool', from_id=full_pool_name, to_type='gke_cluster', to_id=cluster_name, relationship='in_cluster')
