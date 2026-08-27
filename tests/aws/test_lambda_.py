"""Unit tests for lensix_inventory.aws.lambda_ — Lambda functions."""

import json
from unittest.mock import MagicMock, patch

import lensix_inventory.aws.lambda_ as m


class _ResourceNotFoundException(Exception):
    pass


class _NoSuchEntityException(Exception):
    pass


def _fn(**overrides):
    fn = {
        'FunctionArn': 'arn:aws:lambda:us-east-1:1:function:my-fn',
        'FunctionName': 'my-fn',
    }
    fn.update(overrides)
    return fn


def _client_for(functions, policy_by_fn=None, log_groups_by_fn=None,
                 role_get_role_raise=None, role_attached_policies=None,
                 role_policy_docs=None, role_inline_policies=None, role_inline_docs=None):
    """Builds a boto3.client(service, ...) dispatcher for 'lambda', 'logs',
    and 'iam' — every sub-call lambda_.py's gather() now makes, each with a
    safe default so an unconfigured test doesn't hang on an
    unconfigured-MagicMock iteration/dict-access."""
    lambda_client = MagicMock()
    lambda_client.get_paginator.return_value.paginate.return_value = [{'Functions': functions}]
    lambda_client.exceptions.ResourceNotFoundException = _ResourceNotFoundException
    policy_by_fn = policy_by_fn or {}

    def _get_policy(FunctionName):
        if FunctionName in policy_by_fn:
            return {'Policy': json.dumps(policy_by_fn[FunctionName])}
        raise _ResourceNotFoundException('no policy')
    lambda_client.get_policy.side_effect = _get_policy

    logs_client = MagicMock()
    log_groups_by_fn = log_groups_by_fn or {}

    def _describe_log_groups(logGroupNamePrefix):
        name = logGroupNamePrefix.replace('/aws/lambda/', '')
        groups = log_groups_by_fn.get(name, [])
        return {'logGroups': [{'logGroupName': g} for g in groups]}
    logs_client.describe_log_groups.side_effect = _describe_log_groups

    iam_client = MagicMock()
    iam_client.exceptions.NoSuchEntityException = _NoSuchEntityException
    if role_get_role_raise:
        iam_client.get_role.side_effect = lambda RoleName: (_ for _ in ()).throw(role_get_role_raise)
    else:
        iam_client.get_role.return_value = {'Role': {}}
    iam_client.list_attached_role_policies.return_value = {'AttachedPolicies': role_attached_policies or []}
    role_policy_docs = role_policy_docs or {}

    def _get_policy_version(PolicyArn, VersionId):
        return {'PolicyVersion': {'Document': role_policy_docs.get(PolicyArn, {})}}
    iam_client.get_policy_version.side_effect = _get_policy_version
    iam_client.get_policy.side_effect = lambda PolicyArn: {'Policy': {'DefaultVersionId': 'v1'}}
    iam_client.list_role_policies.return_value = {'PolicyNames': role_inline_policies or []}
    role_inline_docs = role_inline_docs or {}

    def _get_role_policy(RoleName, PolicyName):
        return {'PolicyDocument': role_inline_docs.get(PolicyName, {})}
    iam_client.get_role_policy.side_effect = _get_role_policy

    def _client(service, **kwargs):
        return {'lambda': lambda_client, 'logs': logs_client, 'iam': iam_client}[service]
    return _client


class TestRedactEnvironment:
    def test_returns_sorted_variable_names_and_no_hits_for_clean_values(self):
        fn = _fn(Environment={'Variables': {'STAGE': 'prod', 'DEBUG': 'false'}})
        names, hits = m._redact_environment(fn)
        assert names == ['DEBUG', 'STAGE']
        assert hits == []

    def test_detects_a_secret_looking_value(self):
        fn = _fn(Environment={'Variables': {'STRIPE_KEY': 'sk_live_' + 'a' * 24}})
        names, hits = m._redact_environment(fn)
        assert hits == ['Stripe Live API Key']

    def test_no_environment_at_all(self):
        fn = _fn()
        names, hits = m._redact_environment(fn)
        assert names == []
        assert hits == []


class TestDocHasWildcardAdmin:
    def test_true_for_action_and_resource_wildcard(self):
        doc = {'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*'}]}
        assert m._doc_has_wildcard_admin(doc) is True

    def test_true_for_iam_wildcard_action(self):
        doc = {'Statement': [{'Effect': 'Allow', 'Action': ['iam:*'], 'Resource': ['*']}]}
        assert m._doc_has_wildcard_admin(doc) is True

    def test_false_for_a_scoped_action(self):
        doc = {'Statement': [{'Effect': 'Allow', 'Action': 's3:GetObject', 'Resource': '*'}]}
        assert m._doc_has_wildcard_admin(doc) is False

    def test_ignores_deny_statements(self):
        doc = {'Statement': [{'Effect': 'Deny', 'Action': '*', 'Resource': '*'}]}
        assert m._doc_has_wildcard_admin(doc) is False

    def test_none_document_is_falsy(self):
        assert m._doc_has_wildcard_admin(None) is False


class TestGather:
    def test_adds_one_resource_with_env_values_stripped(self):
        w = MagicMock()
        fn = _fn(Environment={'Variables': {'STAGE': 'prod'}})
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 'lambda_function'
        assert kwargs['resource_id'] == 'arn:aws:lambda:us-east-1:1:function:my-fn'
        assert kwargs['resource_name'] == 'my-fn'
        assert kwargs['raw']['Environment']['VariableNames'] == ['STAGE']
        assert 'Variables' not in kwargs['raw']['Environment']

    def test_secret_scan_hits_are_passed_through_to_add_resource(self):
        w = MagicMock()
        fn = _fn(Environment={'Variables': {'STRIPE_KEY': 'sk_live_' + 'a' * 24}})
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['secret_scan_hits'] == ['Stripe Live API Key']

    def test_scope_id_is_the_vpc_id_when_vpc_configured(self):
        w = MagicMock()
        fn = _fn(VpcConfig={'VpcId': 'vpc-1'})
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['scope_id'] == 'vpc-1'

    def test_scope_id_is_none_without_vpc_config(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['scope_id'] is None

    def test_a_function_with_no_environment_block_is_unaffected(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert 'Environment' not in kwargs['raw']

    def test_the_original_function_dict_is_not_mutated(self):
        w = MagicMock()
        fn = _fn(Environment={'Variables': {'STAGE': 'prod'}})
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        assert fn['Environment']['Variables'] == {'STAGE': 'prod'}

    def test_resource_policy_is_merged_in_when_present(self):
        w = MagicMock()
        fn = _fn()
        policy = {'Statement': [{'Effect': 'Allow'}]}
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn], policy_by_fn={'my-fn': policy})):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_ResourcePolicy'] == policy

    def test_resource_policy_is_none_without_one(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_ResourcePolicy'] is None

    def test_has_log_group_true_when_exact_match_present(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn], log_groups_by_fn={'my-fn': ['/aws/lambda/my-fn']})):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_HasLogGroup'] is True

    def test_has_log_group_false_without_a_match(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_HasLogGroup'] is False

    def test_no_role_leaves_role_fields_none(self):
        w = MagicMock()
        fn = _fn()
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleExists'] is None
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is None

    def test_role_exists_true_and_not_admin(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/my-role')
        with patch.object(m.boto3, 'client', side_effect=_client_for([fn])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleExists'] is True
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is False

    def test_role_does_not_exist(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/ghost-role')
        client_fn = _client_for([fn], role_get_role_raise=_NoSuchEntityException('gone'))
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleExists'] is False

    def test_role_has_admin_via_attached_managed_policy_arn(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/admin-role')
        client_fn = _client_for([fn], role_attached_policies=[
            {'PolicyArn': 'arn:aws:iam::aws:policy/AdministratorAccess'},
        ])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is True

    def test_role_has_admin_via_attached_custom_policy_document(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/wildcard-role')
        client_fn = _client_for(
            [fn],
            role_attached_policies=[{'PolicyArn': 'arn:aws:iam::1:policy/my-policy'}],
            role_policy_docs={'arn:aws:iam::1:policy/my-policy': {
                'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*'}]}},
        )
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is True

    def test_role_has_admin_via_inline_policy(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/inline-admin-role')
        client_fn = _client_for(
            [fn],
            role_inline_policies=['my-inline'],
            role_inline_docs={'my-inline': {'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*'}]}},
        )
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is True

    def test_two_functions_sharing_a_role_only_look_it_up_once(self):
        w = MagicMock()
        fn1 = _fn(FunctionName='fn1', FunctionArn='arn:1', Role='arn:aws:iam::1:role/shared-role')
        fn2 = _fn(FunctionName='fn2', FunctionArn='arn:2', Role='arn:aws:iam::1:role/shared-role')
        client_fn = _client_for([fn1, fn2])
        with patch.object(m.boto3, 'client', side_effect=client_fn) as boto_client:
            m.gather('us-east-1', w)
        iam_client = boto_client(service='iam')
        assert iam_client.get_role.call_count == 1
        assert iam_client.list_attached_role_policies.call_count == 1

    def test_a_resource_policy_failure_is_recorded_and_does_not_prevent_the_resource(self):
        w = MagicMock()
        fn = _fn()
        client_fn = _client_for([fn])
        lambda_client = client_fn('lambda')
        lambda_client.get_policy.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'lambda': lambda_client}.get(service, client_fn(service))):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lambda (resource policy:my-fn)' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()

    def test_a_log_group_failure_is_recorded_and_does_not_prevent_the_resource(self):
        w = MagicMock()
        fn = _fn()
        client_fn = _client_for([fn])
        logs_client = client_fn('logs')
        logs_client.describe_log_groups.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'logs': logs_client}.get(service, client_fn(service))):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lambda (log group:my-fn)' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()

    def test_a_role_admin_check_failure_is_recorded_and_does_not_prevent_the_resource(self):
        w = MagicMock()
        fn = _fn(Role='arn:aws:iam::1:role/broken-role')
        client_fn = _client_for([fn])
        iam_client = client_fn('iam')
        iam_client.list_attached_role_policies.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'iam': iam_client}.get(service, client_fn(service))):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lambda (role admin check:broken-role)' for c in w.add_error.call_args_list)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_RoleHasAdminPrivileges'] is None
