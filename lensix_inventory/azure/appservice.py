"""Azure App Service gathering — one merged raw record per app.

Like AWS's S3 module, evaluating an App Service app needs several separate
sub-API calls per app — site configuration, auth settings, application
settings, and (for Function Apps only) the function list — so for gathering
purposes this just calls each of those once per app and merges the results
into one raw record. Runtime-version, TLS/HTTPS, access-restriction, and
auth-presence evaluation is left server-side.

Application setting VALUES are scanned locally for secrets and then
stripped before upload (see common/secrets.py), same treatment as
aws/lambda_.py's `_redact_environment` — only the scan result and the
setting NAMES (never values) are kept, since app settings are a common
place for hardcoded credentials to end up.
"""

from azure.core.exceptions import HttpResponseError
from azure.mgmt.web import WebSiteManagementClient

from ..common.secrets import scan_text_for_secrets
from ._util import resource_group as _resource_group, as_dict as _as_dict


def _try(fn, *args, **kwargs):
    """Best-effort sub-API call, mirroring aws/s3.py's _try — a missing
    site config/auth config/app setting/function list on a given app is
    itself meaningful data (feature not configured), not an error worth
    aborting the whole app's gather for."""
    try:
        return fn(*args, **kwargs)
    except HttpResponseError as e:
        return {'_error': str(e)}
    except Exception as e:
        return {'_error': str(e)}


def get_apps(credential, subscription_id):
    web_client = WebSiteManagementClient(credential, subscription_id)
    return list(web_client.web_apps.list())


def get_configuration(credential, subscription_id, resource_group, app_name):
    web_client = WebSiteManagementClient(credential, subscription_id)
    return _try(web_client.web_apps.get_configuration, resource_group, app_name)


def get_auth_settings(credential, subscription_id, resource_group, app_name):
    web_client = WebSiteManagementClient(credential, subscription_id)
    return _try(web_client.web_apps.get_auth_settings, resource_group, app_name)


def get_application_settings(credential, subscription_id, resource_group, app_name):
    web_client = WebSiteManagementClient(credential, subscription_id)
    return _try(web_client.web_apps.list_application_settings, resource_group, app_name)


def get_functions(credential, subscription_id, resource_group, app_name):
    """Only meaningful for Function Apps (app.kind contains 'functionapp')."""
    web_client = WebSiteManagementClient(credential, subscription_id)
    try:
        return [_as_dict(fn) for fn in web_client.web_apps.list_functions(resource_group, app_name)]
    except Exception:
        return []


def _redact_app_settings(app_settings):
    """Returns (setting_names_only, secret_scan_hits) — the raw values
    themselves are discarded immediately after being scanned, never
    returned/uploaded."""
    if not isinstance(app_settings, dict) and app_settings is not None:
        props = app_settings.properties or {}
    elif isinstance(app_settings, dict) and '_error' not in app_settings:
        props = app_settings
    else:
        props = {}
    hits = []
    for value in props.values():
        hits.extend(scan_text_for_secrets(value or ''))
    return sorted(props.keys()), sorted(set(hits))


def gather(credential, subscription_id, writer):
    for app in get_apps(credential, subscription_id):
        rg = _resource_group(app.id)
        name = app.name

        config = get_configuration(credential, subscription_id, rg, name) if rg else None
        auth = get_auth_settings(credential, subscription_id, rg, name) if rg else None
        app_settings = get_application_settings(credential, subscription_id, rg, name) if rg else None
        setting_names, secret_hits = _redact_app_settings(app_settings)

        raw = _as_dict(app)
        raw['_SiteConfig'] = _as_dict(config)
        raw['_AuthSettings'] = _as_dict(auth)
        raw['_ApplicationSettingNames'] = setting_names

        if rg and app.kind and 'functionapp' in app.kind.lower():
            raw['_Functions'] = get_functions(credential, subscription_id, rg, name)

        writer.add_resource(
            resource_type='app_service',
            region=app.location or 'global',
            resource_id=app.id,
            resource_name=name,
            scope_id=rg,
            raw=raw,
            secret_scan_hits=secret_hits,
            tags=raw.get('tags'),
        )
