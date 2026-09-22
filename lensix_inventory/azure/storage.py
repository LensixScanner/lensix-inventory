"""Azure Storage account gathering — accounts, blob containers, and the
blob/queue/file service property sub-resources.

Only the data-fetching calls are included here (storage_accounts.list,
blob_containers.list, blob_services.get_service_properties,
queue_services.get_service_properties, file_services.get_service_properties)
— public-network-access default, HTTPS-only enforcement, encryption
completeness, blob/log-container public access, blob soft-delete state,
immutability configuration, queue shared-key ACL, trusted-Microsoft-
services bypass, minimum TLS version, queue logging completeness, and SMB
security profile evaluation is left server-side.

Like S3 in `aws/s3.py`, each service-properties call is its own per-account
sub-API fan-out with no single "describe everything" call — they're merged
into the account's raw record as `_BlobServiceProperties`/
`_QueueServiceProperties`/`_FileServiceProperties`.
`file_services.get_service_properties` 404s/409s for account kinds that
don't support file shares (blob-only kinds) — that's treated as "not
applicable" (`None`), not an error. Blob containers get their own resource
type (`blob_container`) since public-access findings are reported against
individual containers by their own ARM id.

Edges: storage_account -> subnet (in_subnet), one per
network_rule_set.virtual_network_rules[] entry — already embedded in the
account's own list() response, no extra API call needed. Despite its
name, VirtualNetworkRule.virtual_network_resource_id is actually a
SUBNET's own ARM id (the same real-world Azure naming quirk as Cosmos
DB's own VirtualNetworkRule.id — both are VNet *service-endpoint* rules,
scoped to a subnet, not the VNet itself). private_endpoint_connections[]
has no persisted target to join to (nothing in this codebase gathers
`private_endpoint` as its own resource type), so that's skipped, same
reasoning as appservice.py's own app_service_plan note. Emitted as read —
the subnet endpoint is owned by network.py's own gather(), a separate
module/container (see its own docstring for this pattern).

Requires: azure-mgmt-storage, azure-core.
"""

from ._util import resource_group as _resource_group

def get_storage_accounts(credential, subscription_id):
    from azure.mgmt.storage import StorageManagementClient
    storage_client = StorageManagementClient(credential, subscription_id)
    return list(storage_client.storage_accounts.list())


def get_blob_containers(credential, subscription_id, rg, account_name):
    from azure.mgmt.storage import StorageManagementClient
    storage_client = StorageManagementClient(credential, subscription_id)
    return list(storage_client.blob_containers.list(rg, account_name))


def get_blob_service_properties(credential, subscription_id, rg, account_name):
    from azure.mgmt.storage import StorageManagementClient
    storage_client = StorageManagementClient(credential, subscription_id)
    try:
        return storage_client.blob_services.get_service_properties(rg, account_name).as_dict()
    except Exception:
        return None


def get_queue_service_properties(credential, subscription_id, rg, account_name):
    from azure.mgmt.storage import StorageManagementClient
    storage_client = StorageManagementClient(credential, subscription_id)
    try:
        return storage_client.queue_services.get_service_properties(rg, account_name).as_dict()
    except Exception:
        return None


def get_file_service_properties(credential, subscription_id, rg, account_name):
    """Returns None for account kinds that don't support file shares (blob-only
    kinds 404/409 here) — that's a meaningful "not applicable" signal, not an
    error."""
    from azure.mgmt.storage import StorageManagementClient
    from azure.core.exceptions import HttpResponseError
    storage_client = StorageManagementClient(credential, subscription_id)
    try:
        return storage_client.file_services.get_service_properties(rg, account_name).as_dict()
    except HttpResponseError as e:
        if e.status_code in (404, 409):
            return None
        raise


def gather(credential, subscription_id, writer):
    try:
        accounts = get_storage_accounts(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='storage:storage_accounts', message=e)
        return

    for account in accounts:
        region = account.location or 'global'
        rg = _resource_group(account.id)

        raw = account.as_dict()
        raw['_BlobServiceProperties'] = get_blob_service_properties(credential, subscription_id, rg, account.name)
        raw['_QueueServiceProperties'] = get_queue_service_properties(credential, subscription_id, rg, account.name)
        try:
            raw['_FileServiceProperties'] = get_file_service_properties(credential, subscription_id, rg, account.name)
        except Exception as e:
            writer.add_error(region=region, source=f'storage:file_services:{account.name}', message=e)
            raw['_FileServiceProperties'] = None

        added = writer.add_resource(
            resource_type='storage_account',
            region=region,
            resource_id=account.id,
            resource_name=account.name,
            scope_id=rg,
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for vnet_rule in ((raw.get('network_rule_set') or {}).get('virtual_network_rules') or []):
                subnet_id = vnet_rule.get('virtual_network_resource_id')
                if subnet_id:
                    writer.add_edge(from_type='storage_account', from_id=account.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')

        try:
            containers = get_blob_containers(credential, subscription_id, rg, account.name)
        except Exception as e:
            writer.add_error(region=region, source=f'storage:blob_containers:{account.name}', message=e)
            continue

        for container in containers:
            # No tags= here: BlobContainer (a sub-resource of the storage
            # account, not the account itself) has no `tags` field on its
            # own SDK model — confirmed absent, same architectural N/A as
            # authorization.py's role_definition.
            writer.add_resource(
                resource_type='blob_container',
                region=region,
                resource_id=container.id,
                resource_name=container.name,
                scope_id=rg,
                raw=container.as_dict(),
            )
