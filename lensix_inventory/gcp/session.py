"""Project/credential/region discovery. This tool runs standalone on the
customer's own machine against whatever local Application Default
Credentials are already configured (`gcloud auth application-default
login`, a GOOGLE_APPLICATION_CREDENTIALS service-account key file, or
ambient workload identity on GCE/GKE/Cloud Run) — no database, no
multi-tenant bookkeeping, no service-account-key-blob plumbing.

Unlike aws/session.py's get_regions() (which every AWS gather module's
gather(region, writer) actually loops over), GCP resources are
project-scoped rather than per-region: every module in this package
enumerates its resources with a single project-scoped aggregatedList/list
call (or a wildcard location like `locations/-`) that already covers every
region/zone in one request, so no gather() function in this package's
other files needs a region/zone list to loop over. get_regions()/
get_zones() are still provided here, mirroring aws/session.py's shape, for
whatever orchestration/reporting use `__init__.py` might have (e.g.
printing scan scope up front) — they are not on the critical path for
gathering itself.
"""

from google.auth import default as google_auth_default
from googleapiclient import discovery

_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']


def get_credentials_and_project_id():
    """Resolve Application Default Credentials and, where available, the
    project ID google-auth infers from them (gcloud's configured project,
    GCE/GKE metadata, or a service-account key file's project_id field)."""
    return google_auth_default(scopes=_SCOPES)


def get_project_id():
    """Best-effort project ID discovery. google-auth's default() already
    resolves this from most credential sources; if it can't (some workload
    identity federation configs carry no project), this raises rather than
    guessing — the caller is expected to let the user pass --project
    explicitly in that case (see cli.py, not yet wired up for GCP)."""
    _, project_id = get_credentials_and_project_id()
    if not project_id:
        raise ValueError(
            "Could not determine a GCP project ID from Application Default "
            "Credentials — pass one explicitly (e.g. --project)."
        )
    return project_id


def get_credentials():
    credentials, _ = get_credentials_and_project_id()
    return credentials


def verify_credentials(credentials, project_id):
    """One cheap, real call to confirm the credentials actually work and
    can see the given project, before spending time on a full gather.

    google_auth_default() resolving successfully only means SOME credential
    source was found locally (a key file, gcloud's login, workload
    identity, ...) — it doesn't mean that credential is still valid or
    authorized for anything, since no network call happens until something
    actually uses it. Without this check, an expired/revoked/unauthorized
    credential wouldn't surface until the first module's gather() call,
    and since every module's failure is caught individually (see
    __init__.py's run()), the result would be dozens of near-identical
    per-module auth errors instead of one clear failure up front — the
    same reason AWS's run() calls get_account_id()
    (sts.get_caller_identity()) before its own loop. Cloud Resource
    Manager's projects().get() is used as the test call because it's
    enabled by default in virtually every GCP project (much of the
    console UI itself depends on it), unlike any single resource-specific
    API this tool might otherwise reach for.
    """
    discovery.build('cloudresourcemanager', 'v1', credentials=credentials).projects().get(
        projectId=project_id
    ).execute()


def get_regions(credentials, project_id):
    """Enumerate this project's available Compute Engine regions. Not
    consumed by any gather module in this package today (see module
    docstring) — provided for orchestration/reporting parity with
    aws/session.py's get_regions()."""
    compute = discovery.build('compute', 'v1', credentials=credentials)
    regions = []
    request = compute.regions().list(project=project_id)
    while request is not None:
        resp = request.execute()
        regions.extend(r['name'] for r in resp.get('items', []))
        request = compute.regions().list_next(previous_request=request, previous_response=resp)
    return regions


def get_zones(credentials, project_id):
    """Enumerate this project's available Compute Engine zones. Same caveat
    as get_regions() — not on the critical path for any gather() call in
    this package, since they all use project-scoped aggregatedList/wildcard
    calls instead of iterating zones themselves."""
    compute = discovery.build('compute', 'v1', credentials=credentials)
    zones = []
    request = compute.zones().list(project=project_id)
    while request is not None:
        resp = request.execute()
        zones.extend(z['name'] for z in resp.get('items', []))
        request = compute.zones().list_next(previous_request=request, previous_response=resp)
    return zones
