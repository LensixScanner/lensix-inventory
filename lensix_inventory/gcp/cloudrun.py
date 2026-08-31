"""Cloud Run gathering — services across all regions in one call, IAM
policy merged into each service's raw record.

Named cloudrun.py (not run.py) deliberately — gcp/uploadchecks/run_import.py
in lensix-scanner-light is the whole-account upload-processing entrypoint
(same role as aws/uploadchecks/run_import.py), so "run" as a module/check-
prefix basename would collide with it. check_ids still use the `run_`
prefix Cloud Run's own product name implies; only the filename differs.

Cloud Run's v1 (Knative-based) Admin API supports a location wildcard
("-") that lists services across every region in a single call, unlike
most other GCP resource types (e.g. KMS, Compute) which require listing
locations first and then paginating per-location. Region comes from each
service's own `metadata.labels['cloud.googleapis.com/location']`, which
Cloud Run always sets.

Public-access evaluation (`run_public`), VPC connector presence
(`run_novpcconnector`), service account identity (`run_defaultserviceaccount`),
ingress setting (`run_ingressall`), and startup CPU boost
(`run_nocpuboost`) are all left server-side — this module only gathers the
raw Service object plus its IAM bindings.
"""

from googleapiclient import discovery


def get_services(run, project_id):
    services = []
    request = run.projects().locations().services().list(parent=f'projects/{project_id}/locations/-')
    while request is not None:
        resp = request.execute()
        services.extend(resp.get('items', []))
        request = run.projects().locations().services().list_next(previous_request=request, previous_response=resp)
    return services


def get_iam_policy(run, service_name):
    resp = run.projects().locations().services().getIamPolicy(resource=service_name).execute()
    return resp.get('bindings', [])


def gather(project_id, credentials, writer):
    run = discovery.build('run', 'v1', credentials=credentials)

    try:
        services = get_services(run, project_id)
    except Exception as e:
        writer.add_error(region='global', source='cloudrun_service', message=e)
        return

    for service in services:
        metadata = service.get('metadata', {})
        name = metadata.get('name', '')
        region = metadata.get('labels', {}).get('cloud.googleapis.com/location', 'global')
        service_name = f'projects/{project_id}/locations/{region}/services/{name}'

        raw = dict(service)
        try:
            raw['_IamPolicyBindings'] = get_iam_policy(run, service_name)
        except Exception as e:
            writer.add_error(region=region, source=f'cloudrun_service:{name}', message=e)

        writer.add_resource(
            resource_type='cloudrun_service',
            region=region,
            resource_id=service_name,
            resource_name=name,
            raw=raw,
            tags=metadata.get('labels'),
        )
