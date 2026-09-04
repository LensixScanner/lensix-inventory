"""Unit tests for lensix_inventory.aws.savingsplans — AWS Savings Plans."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.savingsplans as m


def _savingsplans_client(pages):
    """pages: a list of lists of plan dicts — each list becomes one
    describe_savings_plans response page, chained via nextToken."""
    client = MagicMock()

    def _describe(**kwargs):
        idx = 0 if 'nextToken' not in kwargs else int(kwargs['nextToken'])
        plans = pages[idx]
        next_token = str(idx + 1) if idx + 1 < len(pages) else None
        resp = {'savingsPlans': plans}
        if next_token is not None:
            resp['nextToken'] = next_token
        return resp
    client.describe_savings_plans.side_effect = _describe
    return client


class TestGetSavingsPlans:
    def test_a_single_page_returns_its_plans(self):
        client = _savingsplans_client([[{'savingsPlanId': 'sp-1'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_savings_plans()
        assert result == [{'savingsPlanId': 'sp-1'}]

    def test_paginates_by_hand_via_nexttoken(self):
        client = _savingsplans_client([[{'savingsPlanId': 'sp-1'}], [{'savingsPlanId': 'sp-2'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_savings_plans()
        assert [p['savingsPlanId'] for p in result] == ['sp-1', 'sp-2']

    def test_no_plans_returns_empty_list(self):
        client = _savingsplans_client([[]])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_savings_plans() == []


class TestGather:
    def test_adds_one_resource_per_plan_keyed_by_arn(self):
        w = MagicMock()
        plan = {'savingsPlanId': 'sp-1', 'savingsPlanArn': 'arn:aws:savingsplans::123:savingsplan/sp-1'}
        client = _savingsplans_client([[plan]])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_called_once()
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'savings_plan'
        assert kwargs['region'] == 'global'
        assert kwargs['resource_id'] == 'arn:aws:savingsplans::123:savingsplan/sp-1'
        assert kwargs['resource_name'] == 'sp-1'
        assert kwargs['raw'] == plan

    def test_falls_back_to_the_plan_id_when_no_arn_is_present(self):
        w = MagicMock()
        client = _savingsplans_client([[{'savingsPlanId': 'sp-1'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert w.add_resource.call_args.kwargs['resource_id'] == 'sp-1'

    def test_tags_are_passed_through_as_the_flat_dict_the_api_returns(self):
        w = MagicMock()
        plan = {'savingsPlanId': 'sp-1', 'tags': {'lensix-suppress': 'true'}}
        client = _savingsplans_client([[plan]])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_plans_gathers_nothing(self):
        w = MagicMock()
        client = _savingsplans_client([[]])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_not_called()
