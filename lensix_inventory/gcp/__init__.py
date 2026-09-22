"""GCP provider orchestrator — runs every gather module once against the
target project and returns a populated InventoryWriter.

Every module here takes `gather(project_id, credentials, writer)` — GCP
resource listing is project-scoped (aggregatedList/wildcard-location
calls), not per-region, so no region loop is needed; see session.py's
docstring and each module's own docstring for what it gathers and why.

Each module failure is caught and recorded as an error rather than
aborting the whole run.
"""

from .. import __version__
from ..common.output import InventoryWriter
from . import (
    artifactregistry, bigquery, cloudrun, compute, dns, functions, gke, iam, kms, lb,
    logging, pubsub, secretmanager, sql, storage, vpc, vpcaccess,
)
from .session import get_credentials, get_project_id, verify_credentials

# Bug fix: artifactregistry/cloudrun/secretmanager gather() functions have
# existed as real modules (with their own scanner-light live/upload-path
# counterparts) but were never registered here — the self-hosted CLI's own
# run() never gathered Artifact Registry repos, Cloud Run services, or
# Secret Manager secrets at all, for any self-hosted customer, until now.
# Same bug class as lensix-scanner-light's own dispatcher.py ALL_GCP_MODULES
# list, which had the identical 3-module gap independently.
MODULES = [
    ('artifactregistry', artifactregistry.gather),
    ('bigquery', bigquery.gather),
    ('cloudrun', cloudrun.gather),
    ('compute', compute.gather),
    ('dns', dns.gather),
    ('functions', functions.gather),
    ('gke', gke.gather),
    ('iam', iam.gather),
    ('kms', kms.gather),
    ('lb', lb.gather),
    ('logging', logging.gather),
    ('pubsub', pubsub.gather),
    ('secretmanager', secretmanager.gather),
    ('sql', sql.gather),
    ('storage', storage.gather),
    ('vpc', vpc.gather),
    ('vpcaccess', vpcaccess.gather),
]


def run(regions=None):
    # `regions` is accepted (unused) only for CLI signature parity with the
    # AWS/Azure providers — GCP gathering here is project-scoped, not
    # region-scoped; see module docstring.
    project_id = get_project_id()
    credentials = get_credentials()
    verify_credentials(credentials, project_id)
    writer = InventoryWriter(provider='gcp', account_id=project_id, tool_version=__version__)

    for name, gather_fn in MODULES:
        print(f"[gcp] {name} ...", end=' ', flush=True)
        try:
            gather_fn(project_id, credentials, writer)
            print("done")
        except Exception as e:
            print(f"error: {e}")
            writer.add_error(region='global', source=name, message=e)

    return writer
