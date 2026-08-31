"""Resource group gathering — resource groups and their management locks.

Only the data-fetching calls are included here (resource_groups.list,
management_locks.list_at_resource_group_level) — missing-lock evaluation
is left server-side.

Requires: azure-mgmt-resource.
"""


def get_resource_groups(credential, subscription_id):
    from azure.mgmt.resource import ResourceManagementClient
    resources_client = ResourceManagementClient(credential, subscription_id)
    return list(resources_client.resource_groups.list())


def get_management_locks(credential, subscription_id, rg_name):
    from azure.mgmt.resource.locks import ManagementLockClient
    locks_client = ManagementLockClient(credential, subscription_id)
    return list(locks_client.management_locks.list_at_resource_group_level(rg_name))


def gather(credential, subscription_id, writer):
    try:
        resource_groups = get_resource_groups(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='resources:resource_groups', message=e)
        return

    for rg in resource_groups:
        region = rg.location or 'global'
        rg_raw = rg.as_dict()
        writer.add_resource(
            resource_type='resource_group',
            region=region,
            resource_id=rg.id,
            resource_name=rg.name,
            scope_id=rg.name,
            raw=rg_raw,
            tags=rg_raw.get('tags'),
        )

        try:
            locks = get_management_locks(credential, subscription_id, rg.name)
        except Exception as e:
            writer.add_error(region=region, source=f'resources:locks:{rg.name}', message=e)
            continue

        for lock in locks:
            # No tags= here: ManagementLockObject has no `tags` field on
            # its own SDK model (confirmed — the SDK itself warns and
            # discards it if passed), a control-plane object like
            # authorization's role_definition/policy's policy_assignment.
            writer.add_resource(
                resource_type='management_lock',
                region=region,
                resource_id=lock.id,
                resource_name=lock.name,
                scope_id=rg.name,
                raw=lock.as_dict(),
            )
