"""Unit tests for lensix_inventory.gcp.compute — instance/project metadata
redaction (_redact_metadata) and its integration into gather()'s two
metadata-bearing resource types. Full gather() coverage (instances,
images, snapshots, MIGs/autoscalers) isn't exercised here yet — this file
focuses on the metadata secrets/redaction boundary, the part with real
security-sensitive behavior."""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.compute as m


def _paged(*, aggregated=None, items=None):
    """A MagicMock() request chain that returns one page then stops —
    matches compute.<resource>().list()/aggregatedList() + its own
    *_next(...) pagination method."""
    resp = {}
    if aggregated is not None:
        resp['items'] = aggregated
    if items is not None:
        resp['items'] = items
    request = MagicMock()
    request.execute.return_value = resp
    return request


class TestRedactMetadata:
    def test_keeps_only_key_names_for_unlisted_keys(self):
        items = [{'key': 'startup-script', 'value': '#!/bin/bash\necho hi'}]
        key_names, hits, relevant = m._redact_metadata(items)
        assert key_names == ['startup-script']
        assert relevant == {}

    def test_check_relevant_keys_keep_their_value(self):
        items = [
            {'key': 'serial-port-enable', 'value': 'true'},
            {'key': 'enable-oslogin', 'value': 'false'},
            {'key': 'enable-oslogin-2fa', 'value': 'true'},
            {'key': 'ssh-keys', 'value': 'alice:ssh-rsa AAAA... alice@host'},
        ]
        key_names, hits, relevant = m._redact_metadata(items)
        assert relevant == {
            'serial-port-enable': 'true', 'enable-oslogin': 'false',
            'enable-oslogin-2fa': 'true', 'ssh-keys': 'alice:ssh-rsa AAAA... alice@host',
        }
        assert set(key_names) == set(relevant.keys())

    def test_created_by_keeps_its_value_for_scaling_group_grouping(self):
        mig_path = 'projects/p/zones/us-central1-a/instanceGroupManagers/web-mig'
        items = [{'key': 'created-by', 'value': mig_path}]
        key_names, hits, relevant = m._redact_metadata(items)
        assert relevant == {'created-by': mig_path}
        assert key_names == ['created-by']

    def test_relevant_key_values_are_still_scanned_for_secrets(self):
        secret_value = 'AKIAABCDEFGHIJKLMNOP'
        items = [{'key': 'ssh-keys', 'value': secret_value}]
        with patch.object(m, 'scan_text_for_secrets', return_value=['AWS Access Key ID']):
            key_names, hits, relevant = m._redact_metadata(items)
        assert hits == ['AWS Access Key ID']
        assert relevant == {'ssh-keys': secret_value}

    def test_empty_items_returns_empty_everything(self):
        assert m._redact_metadata(None) == ([], [], {})
        assert m._redact_metadata([]) == ([], [], {})

    def test_a_key_without_a_value_field_defaults_to_empty_string(self):
        items = [{'key': 'enable-oslogin'}]
        key_names, hits, relevant = m._redact_metadata(items)
        assert relevant == {'enable-oslogin': ''}

    def test_secret_hits_are_deduplicated_and_sorted(self):
        items = [{'key': 'a', 'value': 'x'}, {'key': 'b', 'value': 'y'}]
        with patch.object(m, 'scan_text_for_secrets', side_effect=[['Z Hit', 'A Hit'], ['A Hit']]):
            _, hits, _ = m._redact_metadata(items)
        assert hits == ['A Hit', 'Z Hit']


class TestGatherMetadataIntegration:
    def _compute(self, instances=None, project_metadata_items=None):
        compute = MagicMock()
        compute.instances.return_value.aggregatedList.return_value = _paged(
            aggregated={'zones/z1': {'instances': instances or []}})
        compute.instances.return_value.aggregatedList_next.return_value = None
        compute.images.return_value.list.return_value = _paged(items=[])
        compute.images.return_value.list_next.return_value = None
        compute.snapshots.return_value.list.return_value = _paged(items=[])
        compute.snapshots.return_value.list_next.return_value = None
        compute.instanceGroupManagers.return_value.aggregatedList.return_value = _paged(aggregated={})
        compute.instanceGroupManagers.return_value.aggregatedList_next.return_value = None
        compute.autoscalers.return_value.aggregatedList.return_value = _paged(aggregated={})
        compute.autoscalers.return_value.aggregatedList_next.return_value = None
        compute.projects.return_value.get.return_value.execute.return_value = {
            'commonInstanceMetadata': {'items': project_metadata_items or []},
        }
        return compute

    def test_instance_metadata_itemvalues_only_carries_the_relevant_keys(self):
        w = MagicMock()
        instance = {
            'name': 'vm-1', 'status': 'RUNNING', 'zone': 'https://.../zones/z1',
            'metadata': {'items': [
                {'key': 'serial-port-enable', 'value': 'true'},
                {'key': 'startup-script', 'value': 'echo secret-ish'},
            ]},
        }
        compute = self._compute(instances=[instance])
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('proj-1', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        raw = calls['compute_instance'].kwargs['raw']
        assert raw['metadata']['itemValues'] == {'serial-port-enable': 'true'}
        assert set(raw['metadata']['itemKeys']) == {'serial-port-enable', 'startup-script'}
        assert 'items' not in raw['metadata']

    def test_project_metadata_itemvalues_only_carries_the_relevant_keys(self):
        w = MagicMock()
        compute = self._compute(project_metadata_items=[
            {'key': 'ssh-keys', 'value': 'alice:ssh-rsa AAAA...'},
            {'key': 'other-key', 'value': 'irrelevant'},
        ])
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('proj-1', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        raw = calls['compute_project_metadata'].kwargs['raw']
        assert raw['itemValues'] == {'ssh-keys': 'alice:ssh-rsa AAAA...'}
        assert set(raw['itemKeys']) == {'ssh-keys', 'other-key'}


class TestMigInstanceTemplateName:
    def test_reads_the_top_level_reference(self):
        mig = {'instanceTemplate': 'https://www.googleapis.com/compute/v1/projects/p/global/instanceTemplates/web-template'}
        assert m._mig_instance_template_name(mig) == 'web-template'

    def test_falls_back_to_the_first_version_for_a_canary_rollout(self):
        mig = {'versions': [{'instanceTemplate': 'https://.../instanceTemplates/v2-template'}]}
        assert m._mig_instance_template_name(mig) == 'v2-template'

    def test_none_when_neither_is_present(self):
        assert m._mig_instance_template_name({}) is None


class TestNetworkInterfacesHaveAccessConfig:
    def test_true_when_any_interface_has_an_access_config(self):
        nis = [{'accessConfigs': []}, {'accessConfigs': [{'type': 'ONE_TO_ONE_NAT'}]}]
        assert m._network_interfaces_have_access_config(nis) is True

    def test_false_when_no_interface_has_one(self):
        nis = [{'network': 'default'}, {'accessConfigs': []}]
        assert m._network_interfaces_have_access_config(nis) is False

    def test_none_for_no_network_interface_data_at_all(self):
        assert m._network_interfaces_have_access_config(None) is None

    def test_false_for_an_empty_list(self):
        assert m._network_interfaces_have_access_config([]) is False


class TestMigLaunchesWithPublicIp:
    def test_true_when_the_template_has_an_access_config(self):
        mig = {'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = MagicMock()
        compute.instanceTemplates.return_value.get.return_value.execute.return_value = {
            'properties': {'networkInterfaces': [{'accessConfigs': [{'type': 'ONE_TO_ONE_NAT'}]}]},
        }
        assert m._mig_launches_with_public_ip(compute, 'proj-1', mig) is True
        compute.instanceTemplates.return_value.get.assert_called_once_with(project='proj-1', instanceTemplate='web-template')

    def test_false_when_the_template_has_no_access_config(self):
        mig = {'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = MagicMock()
        compute.instanceTemplates.return_value.get.return_value.execute.return_value = {
            'properties': {'networkInterfaces': [{'network': 'default'}]},
        }
        assert m._mig_launches_with_public_ip(compute, 'proj-1', mig) is False

    def test_none_when_the_mig_has_no_template_reference(self):
        compute = MagicMock()
        assert m._mig_launches_with_public_ip(compute, 'proj-1', {}) is None
        compute.instanceTemplates.assert_not_called()

    def test_none_when_the_template_lookup_returns_no_network_interfaces(self):
        mig = {'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = MagicMock()
        compute.instanceTemplates.return_value.get.return_value.execute.return_value = {'properties': {}}
        assert m._mig_launches_with_public_ip(compute, 'proj-1', mig) is None

    def test_a_lookup_failure_propagates_rather_than_being_swallowed(self):
        mig = {'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = MagicMock()
        compute.instanceTemplates.return_value.get.return_value.execute.side_effect = RuntimeError('boom')
        try:
            m._mig_launches_with_public_ip(compute, 'proj-1', mig)
            assert False, 'expected RuntimeError to propagate'
        except RuntimeError as e:
            assert str(e) == 'boom'


class TestGatherMigPublicIpIntegration:
    def _compute_with_mig(self, mig, template_response=None, template_side_effect=None):
        compute = MagicMock()
        compute.instances.return_value.aggregatedList.return_value = _paged(aggregated={})
        compute.instances.return_value.aggregatedList_next.return_value = None
        compute.images.return_value.list.return_value = _paged(items=[])
        compute.images.return_value.list_next.return_value = None
        compute.snapshots.return_value.list.return_value = _paged(items=[])
        compute.snapshots.return_value.list_next.return_value = None
        compute.instanceGroupManagers.return_value.aggregatedList.return_value = _paged(
            aggregated={'zones/us-central1-a': {'instanceGroupManagers': [mig]}})
        compute.instanceGroupManagers.return_value.aggregatedList_next.return_value = None
        compute.autoscalers.return_value.aggregatedList.return_value = _paged(aggregated={})
        compute.autoscalers.return_value.aggregatedList_next.return_value = None
        compute.projects.return_value.get.return_value.execute.return_value = {'commonInstanceMetadata': {'items': []}}
        if template_side_effect is not None:
            compute.instanceTemplates.return_value.get.return_value.execute.side_effect = template_side_effect
        else:
            compute.instanceTemplates.return_value.get.return_value.execute.return_value = template_response or {}
        return compute

    def test_mig_resource_carries_the_public_ip_result(self):
        mig = {'name': 'web-mig', 'selfLink': 'https://.../web-mig',
               'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = self._compute_with_mig(mig, template_response={
            'properties': {'networkInterfaces': [{'accessConfigs': [{'type': 'ONE_TO_ONE_NAT'}]}]},
        })
        w = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('proj-1', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['instance_group_manager'].kwargs['raw']['_InstanceTemplatePublicIp'] is True

    def test_a_template_lookup_failure_is_recorded_not_swallowed(self):
        mig = {'name': 'web-mig', 'selfLink': 'https://.../web-mig',
               'instanceTemplate': 'https://.../instanceTemplates/web-template'}
        compute = self._compute_with_mig(mig, template_side_effect=RuntimeError('AccessDenied'))
        w = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('proj-1', MagicMock(), w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['instance_group_manager'].kwargs['raw']['_InstanceTemplatePublicIp'] is None
        w.add_error.assert_called_once()
        args = w.add_error.call_args
        assert 'web-mig' in args.kwargs['message'] and 'AccessDenied' in args.kwargs['message']
