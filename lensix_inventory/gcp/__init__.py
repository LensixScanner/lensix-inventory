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
from . import bigquery, compute, dns, functions, gke, iam, kms, lb, logging, pubsub, sql, storage, vpc
from .session import get_credentials, get_project_id, verify_credentials

MODULES = [
    ('bigquery', bigquery.gather),
    ('compute', compute.gather),
    ('dns', dns.gather),
    ('functions', functions.gather),
    ('gke', gke.gather),
    ('iam', iam.gather),
    ('kms', kms.gather),
    ('lb', lb.gather),
    ('logging', logging.gather),
    ('pubsub', pubsub.gather),
    ('sql', sql.gather),
    ('storage', storage.gather),
    ('vpc', vpc.gather),
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
