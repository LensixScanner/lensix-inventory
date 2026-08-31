"""BigQuery gathering — one raw record per dataset.

The list call only returns dataset references, so this fetches the full
dataset object per reference to get `location`,
`defaultEncryptionConfiguration`, and `access` (the fields public-access
and CMEK-encryption evaluation need) — that evaluation itself is left
server-side.
"""

from googleapiclient import discovery


def get_dataset_refs(bq, project_id):
    refs = []
    request = bq.datasets().list(projectId=project_id)
    while request is not None:
        resp = request.execute()
        refs.extend(resp.get('datasets', []))
        request = bq.datasets().list_next(previous_request=request, previous_response=resp)
    return refs


def get_dataset(bq, project_id, dataset_id):
    return bq.datasets().get(projectId=project_id, datasetId=dataset_id).execute()


def gather(project_id, credentials, writer):
    bq = discovery.build('bigquery', 'v2', credentials=credentials)

    try:
        refs = get_dataset_refs(bq, project_id)
    except Exception as e:
        writer.add_error(region='global', source='bigquery_dataset', message=e)
        return

    for ref in refs:
        dataset_id = ref['datasetReference']['datasetId']
        try:
            dataset = get_dataset(bq, project_id, dataset_id)
        except Exception as e:
            writer.add_error(region='global', source=f'bigquery_dataset:{dataset_id}', message=e)
            continue

        region = (dataset.get('location') or 'global').lower()
        writer.add_resource(
            resource_type='bigquery_dataset',
            region=region,
            resource_id=dataset_id,
            resource_name=dataset_id,
            raw=dataset,
            tags=dataset.get('labels'),
        )
