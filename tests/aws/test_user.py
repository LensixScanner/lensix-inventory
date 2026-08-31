"""Unit tests for lensix_inventory.aws.user — IAM users."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.user as m


class _LimitExceededException(Exception):
    pass


class _CredentialReportNotReadyException(Exception):
    pass


def _report_csv(rows):
    if not rows:
        return 'user,arn\n'
    header = sorted({k for row in rows for k in row})
    lines = [','.join(header)]
    for row in rows:
        lines.append(','.join(row.get(k, '') for k in header))
    return '\n'.join(lines) + '\n'


def _iam_client(users, policies_by_user=None, groups_by_user=None, fanout_error_users=None,
                 report_rows=None, generate_raises_limit_exceeded=False,
                 report_never_ready=False, get_report_raises=None,
                 allowed_actions_by_arn=None, escalation_error_arns=None, tags_by_user=None):
    # list_user_tags needs an explicit, real {'Tags': [...], 'IsTruncated':
    # False} response — an unconfigured MagicMock's own .get('IsTruncated')
    # is always truthy, which would make get_user_tags()'s own pagination
    # loop spin forever.
    client = MagicMock()
    policies_by_user = policies_by_user or {}
    groups_by_user = groups_by_user or {}
    fanout_error_users = fanout_error_users or set()
    allowed_actions_by_arn = allowed_actions_by_arn or {}
    escalation_error_arns = escalation_error_arns or set()
    tags_by_user = tags_by_user or {}
    client.list_user_tags.side_effect = lambda UserName, **kw: {
        'Tags': tags_by_user.get(UserName, []), 'IsTruncated': False}

    def _simulate(PolicySourceArn, ActionNames, ResourceArns):
        if PolicySourceArn in escalation_error_arns:
            raise RuntimeError('boom')
        allowed = allowed_actions_by_arn.get(PolicySourceArn, [])
        return {'EvaluationResults': [
            {'EvalActionName': a, 'EvalDecision': 'allowed' if a in allowed else 'denied'}
            for a in ActionNames
        ]}
    client.simulate_principal_policy.side_effect = _simulate

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'list_users':
            p.paginate.return_value = [{'Users': users}]
        elif op_name == 'list_attached_user_policies':
            def _paginate(UserName):
                if UserName in fanout_error_users:
                    raise RuntimeError('boom')
                return [{'AttachedPolicies': policies_by_user.get(UserName, [])}]
            p.paginate.side_effect = _paginate
        elif op_name == 'list_groups_for_user':
            def _paginate(UserName):
                if UserName in fanout_error_users:
                    raise RuntimeError('boom')
                return [{'Groups': groups_by_user.get(UserName, [])}]
            p.paginate.side_effect = _paginate
        return p
    client.get_paginator.side_effect = _get_paginator

    client.exceptions.LimitExceededException = _LimitExceededException
    client.exceptions.CredentialReportNotReadyException = _CredentialReportNotReadyException
    if generate_raises_limit_exceeded:
        client.generate_credential_report.side_effect = _LimitExceededException()
    else:
        client.generate_credential_report.return_value = {}

    if get_report_raises:
        client.get_credential_report.side_effect = get_report_raises
    elif report_never_ready:
        client.get_credential_report.side_effect = _CredentialReportNotReadyException()
    else:
        content = _report_csv(report_rows or [])
        client.get_credential_report.return_value = {'Content': content.encode('utf-8')}

    return client


class TestGetEscalationActions:
    def test_returns_only_the_allowed_actions(self):
        client = MagicMock()

        def _simulate(PolicySourceArn, ActionNames, ResourceArns):
            return {'EvaluationResults': [
                {'EvalActionName': a, 'EvalDecision': 'allowed' if a == 'iam:PutUserPolicy' else 'denied'}
                for a in ActionNames
            ]}
        client.simulate_principal_policy.side_effect = _simulate
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_escalation_actions('arn:1') == ['iam:PutUserPolicy']

    def test_nothing_allowed_returns_an_empty_list(self):
        client = MagicMock()
        client.simulate_principal_policy.return_value = {'EvaluationResults': [
            {'EvalActionName': a, 'EvalDecision': 'denied'} for a in m.ESCALATION_ACTIONS
        ]}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_escalation_actions('arn:1') == []

    def test_simulates_against_the_given_arn_and_wildcard_resource(self):
        client = MagicMock()
        client.simulate_principal_policy.return_value = {'EvaluationResults': []}
        with patch.object(m.boto3, 'client', return_value=client):
            m.get_escalation_actions('arn:target')
        call_kwargs = client.simulate_principal_policy.call_args.kwargs
        assert call_kwargs['PolicySourceArn'] == 'arn:target'
        assert call_kwargs['ResourceArns'] == ['*']
        assert call_kwargs['ActionNames'] == m.ESCALATION_ACTIONS


class TestGather:
    def test_adds_one_resource_per_user_with_policies_and_groups_merged_in(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        client = _iam_client(
            [user],
            policies_by_user={'alice': [{'PolicyName': 'AdministratorAccess'}]},
            groups_by_user={'alice': [{'GroupName': 'admins'}]},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 'iam_user'
        assert kwargs['region'] == 'global'
        assert kwargs['resource_id'] == 'arn:aws:iam::1:user/alice'
        assert kwargs['resource_name'] == 'alice'
        assert kwargs['raw']['_AttachedPolicies'] == [{'PolicyName': 'AdministratorAccess'}]
        assert kwargs['raw']['_Groups'] == [{'GroupName': 'admins'}]

    def test_user_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _iam_client([user], tags_by_user={'alice': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_a_fanout_failure_still_records_the_user_with_empty_lists(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        client = _iam_client([user], fanout_error_users={'alice'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'iam_user:arn:aws:iam::1:user/alice'
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_AttachedPolicies'] == []
        assert kwargs['raw']['_Groups'] == []

    def test_escalation_actions_are_merged_in(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:1'}
        client = _iam_client([user], allowed_actions_by_arn={'arn:1': ['iam:PutUserPolicy']})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_EscalationActions'] == ['iam:PutUserPolicy']

    def test_a_clean_user_gets_an_empty_escalation_actions_list(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:1'}
        client = _iam_client([user])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_EscalationActions'] == []

    def test_an_escalation_simulation_failure_is_isolated_from_the_policy_fanout(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:1'}
        client = _iam_client(
            [user],
            policies_by_user={'alice': [{'PolicyName': 'x'}]},
            escalation_error_arns={'arn:1'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'iam_user (escalation simulation:arn:1)' for c in w.add_error.call_args_list)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_EscalationActions'] == []
        # The OTHER fan-out (attached policies) succeeded fine — one
        # fetch's failure doesn't blank out the other's already-fetched
        # data for the same user.
        assert kwargs['raw']['_AttachedPolicies'] == [{'PolicyName': 'x'}]

    def test_the_original_user_dict_is_not_mutated(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:1'}
        client = _iam_client([user])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert '_AttachedPolicies' not in user

    def test_no_users_gathers_nothing(self):
        w = MagicMock()
        client = _iam_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_not_called()

    def test_the_matching_credential_report_row_is_merged_in(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        row = {'user': 'alice', 'arn': 'arn:aws:iam::1:user/alice', 'mfa_active': 'false'}
        client = _iam_client([user], report_rows=[row])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport']['user'] == 'alice'
        assert kwargs['raw']['_CredentialReport']['mfa_active'] == 'false'

    def test_root_account_row_is_not_merged_into_any_user(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        rows = [{'user': 'alice', 'arn': 'arn:aws:iam::1:user/alice'}, {'user': '<root_account>', 'arn': 'arn:aws:iam::1:root'}]
        client = _iam_client([user], report_rows=rows)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport']['user'] == 'alice'

    def test_a_user_with_no_matching_row_gets_none(self):
        w = MagicMock()
        user = {'UserName': 'bob', 'Arn': 'arn:aws:iam::1:user/bob'}
        client = _iam_client([user], report_rows=[{'user': 'alice', 'arn': 'x'}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport'] is None

    def test_a_report_already_in_progress_falls_through_to_polling(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        row = {'user': 'alice', 'arn': 'arn:aws:iam::1:user/alice'}
        client = _iam_client([user], report_rows=[row], generate_raises_limit_exceeded=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_error.assert_not_called()
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport']['user'] == 'alice'

    def test_a_report_that_never_becomes_ready_is_recorded_as_an_error(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        client = _iam_client([user], report_never_ready=True)
        with patch.object(m.boto3, 'client', return_value=client), patch.object(m.time, 'sleep'):
            m.gather(w)
        assert any(c.kwargs['source'] == 'iam_user (credential report)' for c in w.add_error.call_args_list)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport'] is None

    def test_a_generic_report_error_does_not_prevent_users_from_being_gathered(self):
        w = MagicMock()
        user = {'UserName': 'alice', 'Arn': 'arn:aws:iam::1:user/alice'}
        client = _iam_client([user], get_report_raises=RuntimeError('boom'))
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'iam_user (credential report)' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_CredentialReport'] is None
