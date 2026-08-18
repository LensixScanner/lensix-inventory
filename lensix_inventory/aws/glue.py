"""Glue gathering — security configurations and connections (connection
property values redacted).

Two resource types are gathered here (`glue_security_config`,
`glue_connection`). The Data Catalog's account-wide encryption settings
(`get_data_catalog_encryption_settings()`) are deliberately not gathered —
that's a per-account singleton setting, not an enumerable resource, so
there's no resource shape to represent it as.

**Secrets exception**: a Glue connection's `ConnectionProperties` is a free-
form string map that — unless the connection is set up to pull credentials
from Secrets Manager instead — commonly carries a literal `PASSWORD` (and,
for Kafka connections, `KAFKA_CLIENT_KEYSTORE_PASSWORD`/
`KAFKA_CLIENT_KEY_PASSWORD`), or a `JDBC_CONNECTION_URL`/`CONNECTION_URL`
with credentials embedded inline. Every property value is scanned locally
then discarded, same treatment as `aws/lambda_.py`'s `_redact_environment`
— only the property NAMES and the scan result are kept, since there's no
fixed, exhaustive list of which of Glue's many connection-property keys
might be the sensitive one for a given connection type.
"""

import boto3

from ..common.secrets import scan_text_for_secrets


def get_security_configurations(region):
    glue = boto3.client('glue', region_name=region)
    configs = []
    for page in glue.get_paginator('get_security_configurations').paginate():
        configs.extend(page.get('SecurityConfigurations', []))
    return configs


def get_connections(region):
    glue = boto3.client('glue', region_name=region)
    conns = []
    kwargs = {}
    while True:
        resp = glue.get_connections(**kwargs)
        conns.extend(resp.get('ConnectionList', []))
        next_token = resp.get('NextToken')
        if not next_token:
            break
        kwargs['NextToken'] = next_token
    return conns


def _redact_connection(conn):
    """Returns (redacted_conn, secret_scan_hits) — every
    ConnectionProperties value is scanned locally then discarded; only the
    property NAMES remain."""
    raw = dict(conn)
    hits = []
    props = raw.get('ConnectionProperties')
    if isinstance(props, dict):
        for value in props.values():
            hits.extend(scan_text_for_secrets(str(value)))
        raw['ConnectionProperties'] = sorted(props.keys())
    return raw, sorted(set(hits))


def gather(region, writer):
    for cfg in get_security_configurations(region):
        name = cfg['Name']
        writer.add_resource(
            resource_type='glue_security_config',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=cfg,
        )

    for conn in get_connections(region):
        name = conn.get('Name', '')
        raw, secret_hits = _redact_connection(conn)
        writer.add_resource(
            resource_type='glue_connection',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=raw,
            secret_scan_hits=secret_hits,
        )
