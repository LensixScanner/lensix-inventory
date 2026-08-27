"""Unit tests for lensix_inventory.aws.sg — security groups, their rules,
and the cross-service "is this SG referenced anywhere" usage fan-out.

get_attached_sg_ids() aggregates SG references from ~21 independent AWS
services, each wrapped in its own try/except (all silently swallowed
except source #8, Glue, which is the one source that records an error via
writer.add_error — see lib.py's permission-warning matching in
lensix-scanner-light for why Glue alone gets a distinctive error source).
Testing a representative handful of sources (not all 21) plus the
aggregation/resilience behavior is enough to establish confidence in the
pattern; every source follows the same shape.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.sg as m


def _ec2_client_split(sgs, rules):
    # describe_security_groups and describe_security_group_rules both go
    # through get_paginator('...').paginate() with different operation
    # names, so the mock needs to branch on which paginator was requested.
    # gather() now also calls get_attached_sg_ids() internally, which
    # reuses this SAME mocked client (patch.object(..., return_value=))
    # for every one of its ~21 unrelated service calls — get_connections/
    # search_provisioned_products need an explicit empty response (not
    # just an unconfigured get_paginator) or their hand-rolled
    # `while True` pagination loops spin forever on an auto-Mock's
    # always-truthy NextToken/NextPageToken (see TestGetAttachedSgIds's
    # own _client_for for the same note).
    client = MagicMock()

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_security_groups':
            p.paginate.return_value = [{'SecurityGroups': sgs}]
        elif op_name == 'describe_security_group_rules':
            p.paginate.return_value = [{'SecurityGroupRules': rules}]
        else:
            p.paginate.side_effect = RuntimeError('boom')
        return p
    client.get_paginator.side_effect = _get_paginator
    client.get_connections.return_value = {'ConnectionList': []}
    client.search_provisioned_products.return_value = {'ProvisionedProducts': []}
    return client


class TestGather:
    def test_adds_one_resource_per_group_with_its_own_rules_merged_in(self):
        w = MagicMock()
        sg = {'GroupId': 'sg-1', 'GroupName': 'web', 'VpcId': 'vpc-1'}
        rule = {'GroupId': 'sg-1', 'IsEgress': False, 'CidrIpv4': '0.0.0.0/0'}
        client = _ec2_client_split([sg], [rule])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        group_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group']
        assert len(group_calls) == 1
        kwargs = group_calls[0].kwargs
        assert kwargs['resource_id'] == 'sg-1'
        assert kwargs['resource_name'] == 'web'
        assert kwargs['scope_id'] == 'vpc-1'
        assert kwargs['raw']['_Rules'] == [rule]

    def test_a_group_with_no_matching_rules_gets_an_empty_rules_list(self):
        w = MagicMock()
        sg = {'GroupId': 'sg-1', 'GroupName': 'web'}
        client = _ec2_client_split([sg], [])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        group_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group')
        assert group_call.kwargs['raw']['_Rules'] == []

    def test_rules_are_grouped_by_their_own_group_id_not_leaked_across_groups(self):
        w = MagicMock()
        sg1 = {'GroupId': 'sg-1', 'GroupName': 'web'}
        sg2 = {'GroupId': 'sg-2', 'GroupName': 'db'}
        rule1 = {'GroupId': 'sg-1'}
        rule2 = {'GroupId': 'sg-2'}
        client = _ec2_client_split([sg1, sg2], [rule1, rule2])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        raw_by_id = {c.kwargs['resource_id']: c.kwargs['raw'] for c in w.add_resource.call_args_list
                     if c.kwargs['resource_type'] == 'security_group'}
        assert raw_by_id['sg-1']['_Rules'] == [rule1]
        assert raw_by_id['sg-2']['_Rules'] == [rule2]

    def test_a_rules_fetch_failure_does_not_prevent_groups_from_being_gathered(self):
        w = MagicMock()
        sg = {'GroupId': 'sg-1', 'GroupName': 'web'}

        def _get_paginator(op_name):
            p = MagicMock()
            if op_name == 'describe_security_groups':
                p.paginate.return_value = [{'SecurityGroups': [sg]}]
            elif op_name == 'describe_security_group_rules':
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.side_effect = RuntimeError('boom')
            return p
        client = MagicMock()
        client.get_paginator.side_effect = _get_paginator
        client.get_connections.return_value = {'ConnectionList': []}
        client.search_provisioned_products.return_value = {'ProvisionedProducts': []}
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_args_list[0].kwargs['source'] == 'sg (rules)'
        group_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group')
        assert group_call.kwargs['raw']['_Rules'] == []

    def test_falls_back_to_the_group_id_when_group_name_missing(self):
        w = MagicMock()
        sg = {'GroupId': 'sg-1'}
        client = _ec2_client_split([sg], [])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        group_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group')
        assert group_call.kwargs['resource_name'] == 'sg-1'

    def test_the_original_group_dict_is_not_mutated(self):
        w = MagicMock()
        sg = {'GroupId': 'sg-1', 'GroupName': 'web'}
        rule = {'GroupId': 'sg-1'}
        client = _ec2_client_split([sg], [rule])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_Rules' not in sg

    def test_always_adds_a_security_group_usage_singleton(self):
        # Added unconditionally, even with zero security groups and an
        # empty attached-ID set — sg_unused's correlation always has a
        # resource to read.
        w = MagicMock()
        client = _ec2_client_split([], [])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        usage_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group_usage']
        assert len(usage_calls) == 1
        kwargs = usage_calls[0].kwargs
        assert kwargs['resource_id'] == 'attached_ids'
        assert kwargs['raw'] == {'AttachedSecurityGroupIds': []}

    def test_the_usage_singleton_carries_whatever_get_attached_sg_ids_found(self):
        w = MagicMock()
        client = _ec2_client_split([], [])
        with patch.object(m.boto3, 'client', return_value=client), \
             patch.object(m, 'get_attached_sg_ids', return_value={'sg-b', 'sg-a'}):
            m.gather('us-east-1', w)
        usage_call = next(c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'security_group_usage')
        assert usage_call.kwargs['raw'] == {'AttachedSecurityGroupIds': ['sg-a', 'sg-b']}


class TestGetAttachedSgIds:
    def _client_for(self, **overrides):
        def _f(service, **kwargs):
            if service in overrides:
                return overrides[service]
            # Default fallback for every unconfigured service. Most of the
            # 21 sources are gated by get_paginator, which raising handles
            # in one shot. Two sources (Glue, Service Catalog) instead hand-roll
            # a `while True` pagination loop keyed on `resp.get('NextToken'/
            # 'NextPageToken')` — an unconfigured MagicMock's `.get()` returns
            # a fresh (truthy) Mock rather than the real default, so those
            # loops would spin forever without an explicit empty response.
            c = MagicMock()
            c.get_paginator.side_effect = RuntimeError('boom')
            c.get_connections.return_value = {'ConnectionList': []}
            c.search_provisioned_products.return_value = {'ProvisionedProducts': []}
            return c
        return _f

    def test_collects_from_enis(self):
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInterfaces': [{'Groups': [{'GroupId': 'sg-eni'}]}]}]
        with patch.object(m.boto3, 'client', side_effect=self._client_for(ec2=ec2)):
            assert 'sg-eni' in m.get_attached_sg_ids('us-east-1')

    def test_collects_from_lambda_vpc_config(self):
        lam = MagicMock()
        lam.get_paginator.return_value.paginate.return_value = [{'Functions': [{'VpcConfig': {'SecurityGroupIds': ['sg-lambda']}}]}]
        with patch.object(m.boto3, 'client', side_effect=self._client_for(**{'lambda': lam})):
            assert 'sg-lambda' in m.get_attached_sg_ids('us-east-1')

    def test_collects_from_rds_instances_and_clusters(self):
        rds = MagicMock()
        rds.get_paginator.side_effect = lambda op: {
            'describe_db_instances': MagicMock(paginate=MagicMock(return_value=[{'DBInstances': [{'VpcSecurityGroups': [{'VpcSecurityGroupId': 'sg-rds-inst'}]}]}])),
            'describe_db_clusters': MagicMock(paginate=MagicMock(return_value=[{'DBClusters': [{'VpcSecurityGroups': [{'VpcSecurityGroupId': 'sg-rds-cluster'}]}]}])),
        }[op]
        with patch.object(m.boto3, 'client', side_effect=self._client_for(rds=rds)):
            result = m.get_attached_sg_ids('us-east-1')
        assert {'sg-rds-inst', 'sg-rds-cluster'} <= result

    def test_glue_error_is_captured_via_the_writer(self):
        glue = MagicMock()
        glue.get_connections.side_effect = RuntimeError('boom')
        w = MagicMock()
        with patch.object(m.boto3, 'client', side_effect=self._client_for(glue=glue)):
            m.get_attached_sg_ids('us-east-1', writer=w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'sg (glue connections)'

    def test_glue_error_is_silently_swallowed_without_a_writer(self):
        glue = MagicMock()
        glue.get_connections.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', side_effect=self._client_for(glue=glue)):
            result = m.get_attached_sg_ids('us-east-1')
        assert isinstance(result, set)

    def test_glue_collects_from_connection_physical_requirements(self):
        glue = MagicMock()
        glue.get_connections.return_value = {'ConnectionList': [
            {'PhysicalConnectionRequirements': {'SecurityGroupIdList': ['sg-glue']}}]}
        with patch.object(m.boto3, 'client', side_effect=self._client_for(glue=glue)):
            assert 'sg-glue' in m.get_attached_sg_ids('us-east-1')

    def test_every_other_source_being_completely_broken_does_not_prevent_one_working_source(self):
        # Every other service raises (via the default fallback in
        # _client_for, plus an explicitly-broken Glue); only ec2 (ENIs) is
        # configured to succeed.
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInterfaces': [{'Groups': [{'GroupId': 'sg-only'}]}]}]
        glue = MagicMock()
        glue.get_connections.side_effect = RuntimeError('boom')
        w = MagicMock()
        with patch.object(m.boto3, 'client', side_effect=self._client_for(ec2=ec2, glue=glue)):
            result = m.get_attached_sg_ids('us-east-1', writer=w)
        assert result == {'sg-only'}
        # Only Glue's failure is ever recorded; every other source's
        # exception is silently swallowed with no error at all.
        w.add_error.assert_called_once()
