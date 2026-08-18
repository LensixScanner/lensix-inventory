"""Athena gathering — one raw record per workgroup.

`get_workgroup_names`/`get_workgroup` are the two pure fetchers
(list_work_groups + get_work_group); encryption evaluation is pass/fail
logic left server-side. DISABLED workgroups are not skipped here — that
filtering only matters for finding evaluation (a disabled workgroup can't
run queries, so its config is moot for the check), not for inventory, where
the disabled state itself is useful uploaded data.
"""

import boto3


def get_workgroup_names(region):
    client = boto3.client('athena', region_name=region)
    names = []
    kwargs = {}
    while True:
        resp = client.list_work_groups(**kwargs)
        for wg in resp.get('WorkGroups', []):
            names.append(wg['Name'])
        next_token = resp.get('NextToken')
        if not next_token:
            break
        kwargs['NextToken'] = next_token
    return names


def get_workgroup(region, name):
    client = boto3.client('athena', region_name=region)
    return client.get_work_group(WorkGroup=name)['WorkGroup']


def gather(region, writer):
    for name in get_workgroup_names(region):
        try:
            workgroup = get_workgroup(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'athena_workgroup:{name}', message=e)
            continue
        writer.add_resource(
            resource_type='athena_workgroup',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=workgroup,
        )
