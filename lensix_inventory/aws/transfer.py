"""AWS Transfer Family gathering — servers.

Servers are listed (list_servers) then described individually
(describe_server) — the describe result becomes the raw `transfer_server`
record as-is.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


def _server_name(server):
    for tag in server.get('Tags', []):
        if tag['Key'] == 'Name':
            return tag['Value']
    return server['ServerId']


def get_servers(region):
    tf = boto3.client('transfer', region_name=region, config=_BOTO_CFG)
    server_ids = []
    kwargs = {}
    while True:
        resp = tf.list_servers(**kwargs)
        server_ids.extend(s['ServerId'] for s in resp.get('Servers', []))
        next_token = resp.get('NextToken')
        if not next_token:
            break
        kwargs['NextToken'] = next_token
    return server_ids


def describe_server(region, server_id):
    tf = boto3.client('transfer', region_name=region, config=_BOTO_CFG)
    resp = tf.describe_server(ServerId=server_id)
    return resp['Server']


def gather(region, writer):
    for server_id in get_servers(region):
        try:
            server = describe_server(region, server_id)
        except Exception as e:
            writer.add_error(region=region, source=f'transfer_server:{server_id}', message=e)
            continue
        writer.add_resource(
            resource_type='transfer_server',
            region=region,
            resource_id=server_id,
            resource_name=_server_name(server),
            raw=server,
        )
