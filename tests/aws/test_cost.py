"""Unit tests for lensix_inventory.aws.cost — AWS Budgets."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.cost as m


def _budgets_client(budgets, notifications_by_name=None, notification_error_names=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Budgets': budgets}]
    notifications_by_name = notifications_by_name or {}
    notification_error_names = notification_error_names or set()

    def _get_notifications(AccountId, BudgetName):
        if BudgetName in notification_error_names:
            raise RuntimeError('boom')
        return {'Notifications': notifications_by_name.get(BudgetName, [])}
    client.describe_notifications_for_budget.side_effect = _get_notifications
    return client


class TestGather:
    def test_adds_one_resource_per_budget_with_notifications_merged_in(self):
        w = MagicMock()
        budget = {'BudgetName': 'monthly', 'BudgetLimit': {'Amount': '1000'}}
        notifications = [{'NotificationType': 'ACTUAL'}]
        client = _budgets_client([budget], notifications_by_name={'monthly': notifications})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 'budget'
        assert kwargs['region'] == 'global'
        assert kwargs['resource_id'] == 'monthly'
        assert kwargs['raw']['_Notifications'] == notifications
        assert kwargs['raw']['BudgetLimit'] == {'Amount': '1000'}

    def test_a_notifications_failure_for_one_budget_falls_back_to_empty_and_is_recorded(self):
        w = MagicMock()
        budget = {'BudgetName': 'monthly'}
        client = _budgets_client([budget], notification_error_names={'monthly'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_error.assert_called_once()
        assert 'monthly' in w.add_error.call_args.kwargs['source']
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_Notifications'] == []

    def test_the_original_budget_dict_is_not_mutated(self):
        w = MagicMock()
        budget = {'BudgetName': 'monthly'}
        client = _budgets_client([budget], notifications_by_name={'monthly': [{'x': 1}]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        assert '_Notifications' not in budget

    def test_no_budgets_gathers_nothing(self):
        w = MagicMock()
        client = _budgets_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w, '123456789012')
        w.add_resource.assert_not_called()
