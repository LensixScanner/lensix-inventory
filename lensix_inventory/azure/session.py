"""Credential/subscription discovery. This tool runs standalone on the
customer's own machine against their own credentials, so there's no
database, no multi-tenant bookkeeping — just the subscription ID to scan.

Unlike AWS's `sts.get_caller_identity()`, an Azure credential isn't
inherently scoped to one subscription — a principal can have access to
several — so the subscription must be specified explicitly via
AZURE_SUBSCRIPTION_ID rather than guessed.

`SubscriptionClient` comes from the separate `azure-mgmt-subscription`
package, not `azure-mgmt-resource` (which stopped re-exporting it as of
`azure-mgmt-resource` 26.0) — `azure-mgmt-subscription` is listed
explicitly in requirements-azure.txt for that reason.
"""

import os

from azure.identity import DefaultAzureCredential
from azure.mgmt.subscription import SubscriptionClient


def get_credential():
    return DefaultAzureCredential()


def get_subscription_id():
    sub_id = os.environ.get('AZURE_SUBSCRIPTION_ID', '')
    if not sub_id:
        raise ValueError("AZURE_SUBSCRIPTION_ID environment variable is required")
    return sub_id


def verify_credential(credential, subscription_id):
    """One cheap, real call to confirm the credential actually works and
    can see the given subscription, before spending time on a full gather.

    DefaultAzureCredential() construction alone never touches the network —
    it just builds a chain of possible credential sources (env vars,
    managed identity, Azure CLI login, ...) to try lazily on first use.
    Without this check, a bad/expired/unauthorized credential wouldn't
    surface until the first module's gather() call, and since every
    module's failure is caught individually (see __init__.py's run()),
    the result would be dozens of near-identical per-module auth errors
    instead of one clear failure up front — the same reason AWS's run()
    calls get_account_id() (sts.get_caller_identity()) before its own loop.
    """
    SubscriptionClient(credential).subscriptions.get(subscription_id)
