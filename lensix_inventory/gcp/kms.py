"""Cloud KMS gathering — key rings and their crypto keys, IAM policy merged
into each key.

KMS has no single "list everything" call — locations, key rings, and
crypto keys are each their own paginated list, and per-key IAM policy is
its own getIamPolicy fan-out call (needed for public-access evaluation) —
merged into each key's raw record the same way aws/s3.py merges per-bucket
sub-API calls. Public IAM access, missing/weak rotation period, and non-
recommended encryption algorithm evaluation is left server-side.

Both key rings and their crypto keys are gathered as their own resource
types (`kms_crypto_key` alongside the ring), since Lensix needs the raw
crypto-key data to evaluate rotation-period and encryption-algorithm
findings from an uploaded inventory file.
"""

from googleapiclient import discovery


def _region_from_key_name(key_name):
    """Extract location from a KMS key/keyring resource name.
    Format: projects/{project}/locations/{location}/keyRings/{ring}[/cryptoKeys/{key}]
    """
    parts = key_name.split('/')
    try:
        loc_idx = parts.index('locations')
        return parts[loc_idx + 1]
    except (ValueError, IndexError):
        return 'global'


def get_locations(kms, project_id):
    locations = []
    request = kms.projects().locations().list(name=f'projects/{project_id}')
    while request is not None:
        resp = request.execute()
        locations.extend(resp.get('locations', []))
        request = kms.projects().locations().list_next(previous_request=request, previous_response=resp)
    return locations


def get_key_rings(kms, location_name):
    key_rings = []
    request = kms.projects().locations().keyRings().list(parent=location_name)
    while request is not None:
        resp = request.execute()
        key_rings.extend(resp.get('keyRings', []))
        request = kms.projects().locations().keyRings().list_next(previous_request=request, previous_response=resp)
    return key_rings


def get_crypto_keys(kms, key_ring_name):
    keys = []
    request = kms.projects().locations().keyRings().cryptoKeys().list(parent=key_ring_name)
    while request is not None:
        resp = request.execute()
        keys.extend(resp.get('cryptoKeys', []))
        request = kms.projects().locations().keyRings().cryptoKeys().list_next(previous_request=request, previous_response=resp)
    return keys


def get_crypto_key_iam_policy(kms, key_name):
    resp = kms.projects().locations().keyRings().cryptoKeys().getIamPolicy(resource=key_name).execute()
    return resp.get('bindings', [])


def gather(project_id, credentials, writer):
    kms = discovery.build('cloudkms', 'v1', credentials=credentials)

    try:
        locations = get_locations(kms, project_id)
    except Exception as e:
        writer.add_error(region='global', source='kms_keyring', message=e)
        return

    for location in locations:
        location_name = location.get('name', '')
        location_id = location.get('locationId', location_name.split('/')[-1])

        try:
            key_rings = get_key_rings(kms, location_name)
        except Exception as e:
            writer.add_error(region=location_id, source='kms_keyring', message=e)
            continue

        for key_ring in key_rings:
            key_ring_name = key_ring.get('name', '')
            # No tags= here: KeyRing has no `labels` field in the Cloud KMS
            # API at all (only CryptoKey does) — a genuine architectural
            # N/A, not an oversight.
            writer.add_resource(
                resource_type='kms_keyring',
                region=location_id,
                resource_id=key_ring_name,
                resource_name=key_ring_name.split('/')[-1],
                raw=key_ring,
            )

            try:
                keys = get_crypto_keys(kms, key_ring_name)
            except Exception as e:
                writer.add_error(region=location_id, source=f'kms_crypto_key:{key_ring_name}', message=e)
                continue

            for key in keys:
                key_name = key.get('name', '')
                key_label = key_name.split('/')[-1] if key_name else key_name
                region = _region_from_key_name(key_name)

                raw = dict(key)
                try:
                    raw['_IamPolicyBindings'] = get_crypto_key_iam_policy(kms, key_name)
                except Exception as e:
                    writer.add_error(region=region, source=f'kms_crypto_key:{key_name}', message=e)

                writer.add_resource(
                    resource_type='kms_crypto_key',
                    region=region,
                    resource_id=key_name,
                    resource_name=key_label,
                    raw=raw,
                    tags=raw.get('labels'),
                )
