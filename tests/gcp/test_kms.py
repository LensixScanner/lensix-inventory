"""Unit tests for kms.py — key rings and their crypto keys.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. KeyRing has
no `labels` field in the Cloud KMS API at all (only CryptoKey does), a
genuine architectural N/A confirmed against the real API schema.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.kms as m


def _location(location_id='us-central1', name=None):
    return {'name': name or f'projects/p/locations/{location_id}', 'locationId': location_id}


def _key_ring(*, name='projects/p/locations/us-central1/keyRings/r1'):
    return {'name': name}


def _crypto_key(*, name='projects/p/locations/us-central1/keyRings/r1/cryptoKeys/k1', labels=None):
    k = {'name': name}
    if labels is not None:
        k['labels'] = labels
    return k


def _kms_client(locations, key_rings_by_location=None, keys_by_ring=None, iam_by_key=None):
    kms = MagicMock()

    loc_req = MagicMock()
    loc_req.execute.return_value = {'locations': locations}
    kms.projects.return_value.locations.return_value.list.return_value = loc_req
    kms.projects.return_value.locations.return_value.list_next.return_value = None

    key_rings_by_location = key_rings_by_location or {}

    def _kr_list(parent):
        r = MagicMock()
        r.execute.return_value = {'keyRings': key_rings_by_location.get(parent, [])}
        return r
    kms.projects.return_value.locations.return_value.keyRings.return_value.list.side_effect = _kr_list
    kms.projects.return_value.locations.return_value.keyRings.return_value.list_next.return_value = None

    keys_by_ring = keys_by_ring or {}

    def _key_list(parent):
        r = MagicMock()
        r.execute.return_value = {'cryptoKeys': keys_by_ring.get(parent, [])}
        return r
    kms.projects.return_value.locations.return_value.keyRings.return_value.cryptoKeys.return_value.list.side_effect = _key_list
    kms.projects.return_value.locations.return_value.keyRings.return_value.cryptoKeys.return_value.list_next.return_value = None

    iam_by_key = iam_by_key or {}

    def _iam(resource):
        r = MagicMock()
        r.execute.return_value = iam_by_key.get(resource, {'bindings': []})
        return r
    kms.projects.return_value.locations.return_value.keyRings.return_value.cryptoKeys.return_value.getIamPolicy.side_effect = _iam
    return kms


class TestGather:
    def test_adds_a_keyring_and_crypto_key_resource(self):
        loc = _location()
        ring = _key_ring()
        key = _crypto_key()
        kms = _kms_client([loc], {loc['name']: [ring]}, {ring['name']: [key]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=kms):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        ring_call, key_call = writer.add_resource.call_args_list
        assert ring_call.kwargs['resource_type'] == 'kms_keyring'
        assert 'tags' not in ring_call.kwargs
        assert key_call.kwargs['resource_type'] == 'kms_crypto_key'
        assert key_call.kwargs['tags'] is None

    def test_tags_are_passed_through_for_a_crypto_key(self):
        loc = _location()
        ring = _key_ring()
        key = _crypto_key(labels={'lensix-suppress': 'true'})
        kms = _kms_client([loc], {loc['name']: [ring]}, {ring['name']: [key]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=kms):
            m.gather('p', MagicMock(), writer)
        key_call = writer.add_resource.call_args_list[1]
        assert key_call.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_locations_list_failure_is_isolated_and_gather_returns_without_raising(self):
        kms = MagicMock()
        kms.projects.return_value.locations.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=kms):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_no_locations_adds_nothing(self):
        kms = _kms_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=kms):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()
