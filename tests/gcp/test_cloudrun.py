"""Unit tests for cloudrun.py."""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.cloudrun as m


def _service(*, name='checkout-api', region='us-central1', annotations=None,
             template_annotations=None, service_account='', ingress='all', extra_labels=None):
    md_annotations = {'run.googleapis.com/ingress': ingress}
    if annotations is not None:
        md_annotations = annotations
    labels = {'cloud.googleapis.com/location': region}
    labels.update(extra_labels or {})
    return {
        'metadata': {
            'name': name,
            'labels': labels,
            'annotations': md_annotations,
        },
        'spec': {
            'template': {
                'metadata': {'annotations': template_annotations or {}},
                'spec': {'serviceAccountName': service_account, 'containers': [{'image': 'gcr.io/p/img'}]},
            },
        },
    }


def _run_client(services, iam_by_name=None):
    run = MagicMock()
    list_req = MagicMock()
    list_req.execute.return_value = {'items': services}
    run.projects.return_value.locations.return_value.services.return_value.list.return_value = list_req
    run.projects.return_value.locations.return_value.services.return_value.list_next.return_value = None

    iam_by_name = iam_by_name or {}

    def _iam(resource):
        r = MagicMock()
        r.execute.return_value = iam_by_name.get(resource, {'bindings': []})
        return r
    run.projects.return_value.locations.return_value.services.return_value.getIamPolicy.side_effect = _iam
    return run


class TestGetServices:
    def test_returns_items_from_the_response(self):
        svc = _service()
        run = _run_client([svc])
        assert m.get_services(run, 'my-proj') == [svc]

    def test_paginates_via_list_next(self):
        svc1 = _service(name='a')
        svc2 = _service(name='b')
        run = MagicMock()
        req1 = MagicMock()
        req1.execute.return_value = {'items': [svc1]}
        req2 = MagicMock()
        req2.execute.return_value = {'items': [svc2]}
        run.projects.return_value.locations.return_value.services.return_value.list.return_value = req1
        run.projects.return_value.locations.return_value.services.return_value.list_next.side_effect = [req2, None]
        assert m.get_services(run, 'my-proj') == [svc1, svc2]

    def test_uses_the_location_wildcard(self):
        run = _run_client([])
        m.get_services(run, 'my-proj')
        run.projects.return_value.locations.return_value.services.return_value.list.assert_called_with(
            parent='projects/my-proj/locations/-'
        )


class TestGetIamPolicy:
    def test_returns_bindings(self):
        run = _run_client([], iam_by_name={'svc1': {'bindings': [{'role': 'roles/run.invoker', 'members': ['allUsers']}]}})
        assert m.get_iam_policy(run, 'svc1') == [{'role': 'roles/run.invoker', 'members': ['allUsers']}]

    def test_defaults_to_empty_list_without_bindings(self):
        run = _run_client([], iam_by_name={'svc1': {}})
        assert m.get_iam_policy(run, 'svc1') == []


class TestGather:
    def test_adds_one_resource_per_service_with_region_from_label(self):
        svc = _service(name='checkout-api', region='us-east1')
        run = _run_client([svc])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'cloudrun_service'
        assert kwargs['region'] == 'us-east1'
        assert kwargs['resource_name'] == 'checkout-api'
        assert kwargs['resource_id'] == 'projects/my-proj/locations/us-east1/services/checkout-api'

    def test_merges_iam_bindings_into_raw(self):
        svc = _service(name='checkout-api', region='us-central1')
        bindings = [{'role': 'roles/run.invoker', 'members': ['allUsers']}]
        run = _run_client([svc], iam_by_name={
            'projects/my-proj/locations/us-central1/services/checkout-api': {'bindings': bindings},
        })
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        raw = writer.add_resource.call_args.kwargs['raw']
        assert raw['_IamPolicyBindings'] == bindings

    def test_a_services_list_failure_is_isolated_and_gather_returns_without_raising(self):
        run = MagicMock()
        run.projects.return_value.locations.return_value.services.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_getiampolicy_failure_for_one_service_does_not_abort_the_others(self):
        svc1 = _service(name='bad')
        svc2 = _service(name='good')
        run = _run_client([svc1, svc2])

        def _iam(resource):
            if 'bad' in resource:
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = {'bindings': []}
            return r
        run.projects.return_value.locations.return_value.services.return_value.getIamPolicy.side_effect = _iam

        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        assert writer.add_error.call_count == 1

    def test_tags_are_passed_through_from_labels_alongside_the_system_location_label(self):
        svc = _service(name='checkout-api', region='us-east1', extra_labels={'lensix-suppress': 'true'})
        run = _run_client([svc])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        tags = writer.add_resource.call_args.kwargs['tags']
        assert tags['lensix-suppress'] == 'true'
        assert tags['cloud.googleapis.com/location'] == 'us-east1'

    def test_no_services_adds_nothing(self):
        run = _run_client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=run):
            m.gather('my-proj', MagicMock(), writer)
        writer.add_resource.assert_not_called()
        writer.add_error.assert_not_called()
