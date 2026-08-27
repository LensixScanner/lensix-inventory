"""Unit tests for lensix_inventory.azure.policy — subscription-level policy assignments."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.policy as m


def _assignment(display_name='Allowed locations', rid='/subscriptions/s1/providers/Microsoft.Authorization/policyAssignments/pa1', name='pa1'):
    a = MagicMock()
    a.display_name = display_name
    a.id = rid
    a.name = name
    a.as_dict.return_value = {'id': rid, 'name': name}
    return a


class TestGather:
    def test_adds_one_resource_per_assignment_named_from_display_name(self):
        w = MagicMock()
        assignment = _assignment()
        client = MagicMock()
        client.policy_assignments.list.return_value = [assignment]
        with patch('azure.mgmt.resource.PolicyClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='policy_assignment', region='global', resource_id=assignment.id,
            resource_name='Allowed locations', raw={'id': assignment.id, 'name': 'pa1'},
        )

    def test_falls_back_to_the_technical_name_without_a_display_name(self):
        w = MagicMock()
        assignment = _assignment(display_name=None)
        client = MagicMock()
        client.policy_assignments.list.return_value = [assignment]
        with patch('azure.mgmt.resource.PolicyClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'pa1'

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        client = MagicMock()
        client.policy_assignments.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.resource.PolicyClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['region'] == 'global'
        assert w.add_error.call_args.kwargs['source'] == 'policy:policy_assignments'
        assert isinstance(w.add_error.call_args.kwargs['message'], RuntimeError)
        w.add_resource.assert_not_called()

    def test_no_assignments_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.policy_assignments.list.return_value = []
        with patch('azure.mgmt.resource.PolicyClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
