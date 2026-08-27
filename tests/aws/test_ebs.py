"""Unit tests for lensix_inventory.aws.ebs — volumes, snapshots, AMIs, and region encryption default."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ebs as m


def _client(volumes=None, snapshots=None, public_snapshots=None, amis=None,
            enc_default=True, enc_default_raises=False,
            volumes_raise=False, snapshots_raise=False, amis_raise=False):
    client = MagicMock()

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_volumes':
            if volumes_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'Volumes': volumes or []}]
        elif op_name == 'describe_snapshots':
            def _paginate(OwnerIds, RestorableByUserIds=None):
                if RestorableByUserIds is not None:
                    return [{'Snapshots': [{'SnapshotId': sid} for sid in (public_snapshots or [])]}]
                if snapshots_raise:
                    raise RuntimeError('boom')
                return [{'Snapshots': snapshots or []}]
            p.paginate.side_effect = _paginate
        return p
    client.get_paginator.side_effect = _get_paginator

    if amis_raise:
        client.describe_images.side_effect = RuntimeError('boom')
    else:
        client.describe_images.return_value = {'Images': amis or []}

    if enc_default_raises:
        client.get_ebs_encryption_by_default.side_effect = RuntimeError('boom')
    else:
        client.get_ebs_encryption_by_default.return_value = {'EbsEncryptionByDefault': enc_default}
    return client


class TestTagName:
    def test_uses_the_name_tag(self):
        assert m._tag_name([{'Key': 'Name', 'Value': 'my-volume'}], 'vol-1') == 'my-volume'

    def test_falls_back_without_a_name_tag(self):
        assert m._tag_name([{'Key': 'env', 'Value': 'prod'}], 'vol-1') == 'vol-1'

    def test_falls_back_with_no_tags_at_all(self):
        assert m._tag_name(None, 'vol-1') == 'vol-1'


class TestGetEbsEncryptionByDefault:
    def test_returns_none_on_failure(self):
        client = _client(enc_default_raises=True)
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_ebs_encryption_by_default('us-east-1') is None


class TestGetPublicSnapshotIds:
    def test_returns_empty_set_on_failure(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_public_snapshot_ids('us-east-1') == set()


class TestGather:
    def test_adds_the_region_settings_resource_when_available(self):
        w = MagicMock()
        client = _client(enc_default=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ebs_region_settings'].kwargs['resource_id'] == 'us-east-1'
        assert calls['ebs_region_settings'].kwargs['raw'] == {'EbsEncryptionByDefault': True}

    def test_no_region_settings_resource_when_the_fetch_fails(self):
        w = MagicMock()
        client = _client(enc_default_raises=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert 'ebs_region_settings' not in calls

    def test_adds_one_resource_per_volume_named_from_its_tag(self):
        w = MagicMock()
        vol = {'VolumeId': 'vol-1', 'Tags': [{'Key': 'Name', 'Value': 'root-disk'}]}
        client = _client(volumes=[vol])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ebs_volume'].kwargs['resource_name'] == 'root-disk'

    def test_a_volumes_failure_does_not_prevent_the_others_from_being_gathered(self):
        w = MagicMock()
        client = _client(volumes_raise=True, amis=[{'ImageId': 'ami-1'}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ebs (volumes)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ebs_ami' in calls

    def test_a_snapshot_is_marked_public_when_restorable_by_all(self):
        w = MagicMock()
        snap = {'SnapshotId': 'snap-1'}
        client = _client(snapshots=[snap], public_snapshots=['snap-1'])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ebs_snapshot'].kwargs['raw']['_Public'] is True

    def test_a_snapshot_not_in_the_public_set_is_marked_private(self):
        w = MagicMock()
        snap = {'SnapshotId': 'snap-1'}
        client = _client(snapshots=[snap], public_snapshots=[])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ebs_snapshot'].kwargs['raw']['_Public'] is False

    def test_a_snapshots_failure_does_not_prevent_the_others_from_being_gathered(self):
        w = MagicMock()
        client = _client(snapshots_raise=True, amis=[{'ImageId': 'ami-1'}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ebs (snapshots)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ebs_ami' in calls

    def test_adds_one_resource_per_ami(self):
        w = MagicMock()
        ami = {'ImageId': 'ami-1', 'Name': 'my-ami'}
        client = _client(amis=[ami])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ebs_ami'].kwargs['resource_id'] == 'ami-1'

    def test_an_amis_failure_does_not_prevent_the_others_from_being_gathered(self):
        w = MagicMock()
        vol = {'VolumeId': 'vol-1'}
        client = _client(volumes=[vol], amis_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ebs (amis)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ebs_volume' in calls

    def test_the_original_snapshot_dict_is_not_mutated(self):
        w = MagicMock()
        snap = {'SnapshotId': 'snap-1'}
        client = _client(snapshots=[snap])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_Public' not in snap
