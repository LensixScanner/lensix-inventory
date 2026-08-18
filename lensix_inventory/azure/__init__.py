"""Azure provider orchestrator — runs every gather module once against the
target subscription and returns a populated InventoryWriter.

Unlike AWS, no per-region looping is needed: every module here takes
`gather(credential, subscription_id, writer)` — ARM resource listing is
subscription-wide, with region/location just a property on each returned
resource, not a separate API endpoint to loop over (see each module's own
docstring for what it gathers and why).

Each module failure is caught and recorded as an error rather than
aborting the whole run.
"""

from .. import __version__
from ..common.output import InventoryWriter
from . import (
    acr, activitylog, aks, apimgmt, appgateway, appservice, authorization,
    bastion, cdn, conditionalaccess, containerapps, cosmosdb, datafactory,
    datalake, defender, eventgrid, eventhub, frontdoor, keyvault, lb,
    monitor, mysql, network, networkwatcher, nsg, policy, postgresql,
    redis, resources, rsv, securitycenter, servicebus, sql, storage,
    synapse, vm,
)
from .session import get_credential, get_subscription_id, verify_credential

MODULES = [
    ('acr', acr.gather),
    ('activitylog', activitylog.gather),
    ('aks', aks.gather),
    ('apimgmt', apimgmt.gather),
    ('appgateway', appgateway.gather),
    ('appservice', appservice.gather),
    ('authorization', authorization.gather),
    ('bastion', bastion.gather),
    ('cdn', cdn.gather),
    ('conditionalaccess', conditionalaccess.gather),
    ('containerapps', containerapps.gather),
    ('cosmosdb', cosmosdb.gather),
    ('datafactory', datafactory.gather),
    ('datalake', datalake.gather),
    ('defender', defender.gather),
    ('eventgrid', eventgrid.gather),
    ('eventhub', eventhub.gather),
    ('frontdoor', frontdoor.gather),
    ('keyvault', keyvault.gather),
    ('lb', lb.gather),
    ('monitor', monitor.gather),
    ('mysql', mysql.gather),
    ('network', network.gather),
    ('networkwatcher', networkwatcher.gather),
    ('nsg', nsg.gather),
    ('policy', policy.gather),
    ('postgresql', postgresql.gather),
    ('redis', redis.gather),
    ('resources', resources.gather),
    ('rsv', rsv.gather),
    ('securitycenter', securitycenter.gather),
    ('servicebus', servicebus.gather),
    ('sql', sql.gather),
    ('storage', storage.gather),
    ('synapse', synapse.gather),
    ('vm', vm.gather),
]


def run(regions=None):
    # `regions` is accepted (unused) only for CLI signature parity with the
    # AWS/GCP providers — Azure gathering here is subscription-wide, not
    # region-scoped; see module docstring.
    credential = get_credential()
    subscription_id = get_subscription_id()
    verify_credential(credential, subscription_id)
    writer = InventoryWriter(provider='azure', account_id=subscription_id, tool_version=__version__)

    for name, gather_fn in MODULES:
        print(f"[azure] {name} ...", end=' ', flush=True)
        try:
            gather_fn(credential, subscription_id, writer)
            print("done")
        except Exception as e:
            print(f"error: {e}")
            writer.add_error(region='global', source=name, message=e)

    return writer
