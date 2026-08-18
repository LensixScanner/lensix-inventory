"""Azure Container Apps gathering.

`container_apps.list_by_subscription()` already returns everything most
evaluation needs (configuration.ingress, identity) — that evaluation itself
is left server-side. Auth configuration needs a per-app sub-call —
`container_apps_auth_configs.get(rg, name, 'current')` — a plain get call,
so it's included too and merged into each app's raw record as
`_AuthConfig`.

Container env var VALUES are scanned locally for secrets and then stripped
before upload (see common/secrets.py), same treatment as
aws/lambda_.py's `_redact_environment` — only the scan result and the
variable names/secretRefs (never inline secret values) are kept, since
these are a common place for hardcoded credentials to end up. Env vars
sourced from a Container Apps secret (`secretRef` set) are left alone since
their value isn't inline plaintext in the first place. Covers both
`containers[]` and `initContainers[]`.

The scrub happens on the *typed model object*, before `.as_dict()` is ever
called, not by reaching into `.as_dict()`'s output afterward: this SDK
generation's `.as_dict()` nests `template` under a top-level `properties`
key — `properties.template` — not the flat `template` attribute name a
lookup on the *output* dict would assume, while attribute access on the
*object* itself (`app.template`) works fine regardless. Assigning `None` to
`env.value` is what actually clears it before serialization — a dict-style
`.pop()` does not reliably propagate through these models.

Requires `azure-mgmt-appcontainers`, treated as an optional import here
(it may not be installed everywhere) via a try/except.
"""

from ._util import resource_group as _resource_group, as_dict as _as_dict

try:
    from azure.mgmt.appcontainers import ContainerAppsAPIClient
    _IMPORT_OK = True
except ImportError as _import_err:
    _IMPORT_OK = False
    _IMPORT_ERR = str(_import_err)

from azure.core.exceptions import HttpResponseError

from ..common.secrets import scan_text_for_secrets

def get_apps(credential, subscription_id):
    client = ContainerAppsAPIClient(credential, subscription_id)
    return list(client.container_apps.list_by_subscription())


def get_auth_config(credential, subscription_id, resource_group, app_name):
    client = ContainerAppsAPIClient(credential, subscription_id)
    try:
        return _as_dict(client.container_apps_auth_configs.get(resource_group, app_name, 'current'))
    except HttpResponseError:
        return None
    except Exception:
        return None


def _redact_env_secrets(app):
    """Scans every non-secretRef env var value across all containers AND
    initContainers in the app's template for hardcoded secrets, then
    clears each one's `value` on the live object (so a subsequent
    .as_dict() call never serializes it, regardless of where that ends up
    nesting `template`) — the raw values themselves are discarded
    immediately after being scanned, never returned/uploaded. Returns the
    distinct matched rule names.

    Must run BEFORE .as_dict() is called on `app` — see module docstring
    for why reaching into .as_dict()'s output afterward doesn't work."""
    hits = []
    tmpl = app.template
    if tmpl is None:
        return hits
    for containers in (tmpl.containers, tmpl.init_containers):
        for container in (containers or []):
            for env in (container.env or []):
                if getattr(env, 'secret_ref', None):
                    continue
                hits.extend(scan_text_for_secrets(getattr(env, 'value', None) or ''))
                env.value = None  # not .pop() — see module docstring
    return sorted(set(hits))


def gather(credential, subscription_id, writer):
    if not _IMPORT_OK:
        writer.add_error(
            region='global', source='containerapps',
            message=f"azure-mgmt-appcontainers is not installed: {_IMPORT_ERR}",
        )
        return

    for app in get_apps(credential, subscription_id):
        secret_hits = _redact_env_secrets(app)
        raw = _as_dict(app)

        rg = _resource_group(app.id)
        if rg:
            raw['_AuthConfig'] = get_auth_config(credential, subscription_id, rg, app.name)

        writer.add_resource(
            resource_type='container_app',
            region=app.location or 'global',
            resource_id=app.id,
            resource_name=app.name,
            scope_id=rg,
            raw=raw,
            secret_scan_hits=secret_hits,
        )
