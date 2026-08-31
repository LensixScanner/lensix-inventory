"""Azure AD Conditional Access policy gathering.

Unlike every other module in this tool, this one doesn't use an
azure-mgmt-* client at all — Conditional Access policies are Entra ID
(Azure AD) objects only reachable via the Microsoft Graph REST API, so it
acquires a Graph-scoped token from the same `credential` and does a plain
GET (`get_conditional_access_policies`). Checking for a policy that
enforces sign-in frequency is finding evaluation over the fetched list and
stays server-side.

If Graph access isn't consented, `get_conditional_access_policies` simply
raises and `gather()` records an error via `writer.add_error()` instead,
same as any other gathering failure in this tool.

Requires only `azure-identity` (for `credential.get_token()`) — no
additional azure-mgmt-* package.
"""

import json
import urllib.error
import urllib.request

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


def _graph_get(token, path):
    """GET from Microsoft Graph API. Returns parsed JSON."""
    req = urllib.request.Request(
        f'{GRAPH_BASE}{path}',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_conditional_access_policies(credential):
    token = credential.get_token('https://graph.microsoft.com/.default').token
    data = _graph_get(token, '/identity/conditionalAccess/policies')
    return data.get('value', [])


def gather(credential, subscription_id, writer):
    try:
        policies = get_conditional_access_policies(credential)
    except Exception as e:
        writer.add_error(region='global', source='conditionalaccess', message=e)
        return

    for policy in policies:
        policy_id = policy.get('id')
        # No tags= here: conditionalAccessPolicy is a Microsoft Graph
        # (Entra ID) object, not an ARM resource — Entra ID objects have
        # no `tags` concept at all, unlike ARM's ubiquitous `tags`
        # property, so there's genuinely nothing to pass through. Same
        # architectural N/A class as authorization.py's role_definition/
        # policy.py's policy_assignment (see docs/tag-suppressions.md).
        # The module's own single check (conditionalaccess_
        # nosigninfrequency) is also a subscription-wide aggregate over
        # the whole policy list, not per-policy, so per-check tag
        # suppression couldn't apply here even if tags existed.
        writer.add_resource(
            resource_type='conditional_access_policy',
            region='global',
            resource_id=policy_id,
            resource_name=policy.get('displayName') or policy_id,
            raw=policy,
        )
