"""Cloud IAM gathering — project IAM policy, service accounts (with key
metadata), and workload identity pool providers.

Only the data-fetching calls are included here (project getIamPolicy,
service account list, per-service-account key list, workload identity
pool + provider list) — personal Gmail accounts, direct user role grants,
default-service-account role grants, privileged/admin service accounts,
service-account impersonation grants, combined admin+KMS roles, audit-
logging exemptions, combined SA-User+SA-Admin roles, non-default SA with
admin access, missing full audit logging, user-managed SA keys, stale SA
key rotation, and workload identity providers with no attribute condition
evaluation is left server-side.

Service account KEY records here carry only key metadata (name, keyType,
validAfterTime, keyAlgorithm, ...) as returned by
`serviceAccounts().keys().list()` — the Cloud IAM API never returns private
key material from a list call, so there is no secret-redaction step needed
here (unlike compute.py's instance metadata or functions.py's environment
variables, which are genuinely free-text fields the API hands back
verbatim).

The project's IAM policy (bindings + auditConfigs) is gathered once here as
a single `iam_policy` resource. Audit-logging evaluation (missing/exempted
audit logging) reads that exact same `auditConfigs` data — rather than
fetching and uploading it twice, this tool gathers it only here; see
logging.py's docstring for the corresponding note.
"""

from googleapiclient import discovery


def get_iam_policy(crm, project_id):
    return crm.projects().getIamPolicy(resource=project_id, body={}).execute()


def get_service_accounts(iam, project_id):
    resp = iam.projects().serviceAccounts().list(name=f'projects/{project_id}').execute()
    return resp.get('accounts', [])


def get_service_account_keys(iam, sa_name):
    resp = iam.projects().serviceAccounts().keys().list(name=sa_name).execute()
    return resp.get('keys', [])


def get_workload_identity_pools(iam, project_id):
    resp = iam.projects().locations().workloadIdentityPools().list(
        parent=f'projects/{project_id}/locations/global'
    ).execute()
    return resp.get('workloadIdentityPools', [])


def get_workload_identity_providers(iam, pool_name):
    resp = iam.projects().locations().workloadIdentityPools().providers().list(parent=pool_name).execute()
    return resp.get('workloadIdentityPoolProviders', [])


def gather(project_id, credentials, writer):
    crm = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
    iam = discovery.build('iam', 'v1', credentials=credentials)

    # --- Project IAM policy (bindings + audit configs) ---
    try:
        policy = get_iam_policy(crm, project_id)
        writer.add_resource(
            resource_type='iam_policy',
            region='global',
            resource_id=f'{project_id}/iam',
            resource_name=project_id,
            raw=policy,
        )
    except Exception as e:
        writer.add_error(region='global', source='iam_policy', message=e)

    # --- Service accounts and their keys ---
    try:
        service_accounts = get_service_accounts(iam, project_id)
    except Exception as e:
        writer.add_error(region='global', source='service_account', message=e)
        service_accounts = []

    for sa in service_accounts:
        sa_name = sa.get('name', '')
        sa_email = sa.get('email', sa_name)

        raw = dict(sa)
        try:
            raw['_Keys'] = get_service_account_keys(iam, sa_name)
        except Exception as e:
            writer.add_error(region='global', source=f'service_account:{sa_email}', message=e)
            raw['_Keys'] = []

        writer.add_resource(
            resource_type='service_account',
            region='global',
            resource_id=sa_name,
            resource_name=sa_email,
            raw=raw,
        )

    # --- Workload identity pool providers ---
    try:
        pools = get_workload_identity_pools(iam, project_id)
    except Exception as e:
        writer.add_error(region='global', source='workload_identity_pool_provider', message=e)
        pools = []

    for pool in pools:
        pool_name = pool.get('name', '')
        try:
            providers = get_workload_identity_providers(iam, pool_name)
        except Exception as e:
            writer.add_error(region='global', source=f'workload_identity_pool_provider:{pool_name}', message=e)
            continue

        for provider in providers:
            provider_name = provider.get('name', '')
            writer.add_resource(
                resource_type='workload_identity_pool_provider',
                region='global',
                resource_id=provider_name,
                resource_name=provider_name.split('/')[-1],
                raw=provider,
            )
