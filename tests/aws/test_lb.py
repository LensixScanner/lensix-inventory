"""Unit tests for lensix_inventory.aws.lb — Classic ELBs and modern ALB/NLBs."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.lb as m


class _WAFNonexistentItemException(Exception):
    pass


def _clients(classic_lbs=None, classic_attrs=None, classic_health=None, classic_raise=False,
             modern_lbs=None, modern_raise=False, lb_attrs=None,
             listeners_by_arn=None, target_groups_by_arn=None,
             tg_attrs_by_arn=None, target_health_by_arn=None,
             web_acl_by_arn=None, web_acl_not_found_arns=None,
             all_target_groups=None, all_target_groups_raise=False,
             classic_tags_by_name=None, elbv2_tags_by_arn=None):
    elb = MagicMock()
    if classic_raise:
        elb.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
    else:
        elb.get_paginator.return_value.paginate.return_value = [{'LoadBalancerDescriptions': classic_lbs or []}]
    elb.describe_load_balancer_attributes.return_value = {'LoadBalancerAttributes': classic_attrs or {}}
    elb.describe_instance_health.return_value = {'InstanceStates': classic_health or []}
    classic_tags_by_name = classic_tags_by_name or {}
    elb.describe_tags.side_effect = lambda LoadBalancerNames: {
        'TagDescriptions': [{'LoadBalancerName': n, 'Tags': classic_tags_by_name.get(n, [])} for n in LoadBalancerNames]
    }

    elbv2 = MagicMock()

    def _elbv2_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_load_balancers':
            if modern_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'LoadBalancers': modern_lbs or []}]
        elif op_name == 'describe_target_groups':
            def _paginate(LoadBalancerArn=None):
                if LoadBalancerArn is not None:
                    return [{'TargetGroups': (target_groups_by_arn or {}).get(LoadBalancerArn, [])}]
                if all_target_groups_raise:
                    raise RuntimeError('boom')
                return [{'TargetGroups': all_target_groups or []}]
            p.paginate.side_effect = _paginate
        return p
    elbv2.get_paginator.side_effect = _elbv2_paginator

    lb_attrs = lb_attrs or {}
    elbv2.describe_load_balancer_attributes.side_effect = lambda LoadBalancerArn: {
        'Attributes': [{'Key': k, 'Value': v} for k, v in lb_attrs.get(LoadBalancerArn, {}).items()]
    }
    listeners_by_arn = listeners_by_arn or {}
    elbv2.describe_listeners.side_effect = lambda LoadBalancerArn: {'Listeners': listeners_by_arn.get(LoadBalancerArn, [])}

    tg_attrs_by_arn = tg_attrs_by_arn or {}
    elbv2.describe_target_group_attributes.side_effect = lambda TargetGroupArn: {
        'Attributes': [{'Key': k, 'Value': v} for k, v in tg_attrs_by_arn.get(TargetGroupArn, {}).items()]
    }
    target_health_by_arn = target_health_by_arn or {}
    elbv2.describe_target_health.side_effect = lambda TargetGroupArn: {
        'TargetHealthDescriptions': target_health_by_arn.get(TargetGroupArn, [])
    }
    elbv2_tags_by_arn = elbv2_tags_by_arn or {}
    elbv2.describe_tags.side_effect = lambda ResourceArns: {
        'TagDescriptions': [{'ResourceArn': a, 'Tags': elbv2_tags_by_arn.get(a, [])} for a in ResourceArns]
    }

    wafv2 = MagicMock()
    wafv2.exceptions.WAFNonexistentItemException = _WAFNonexistentItemException
    web_acl_by_arn = web_acl_by_arn or {}
    web_acl_not_found_arns = web_acl_not_found_arns or set()

    def _get_web_acl(ResourceArn):
        if ResourceArn in web_acl_not_found_arns:
            raise _WAFNonexistentItemException()
        return {'WebACL': web_acl_by_arn.get(ResourceArn)}
    wafv2.get_web_acl_for_resource.side_effect = _get_web_acl

    def _client(service, region_name=None):
        return {'elb': elb, 'elbv2': elbv2, 'wafv2': wafv2}[service]
    return _client


class TestGather:
    def test_adds_one_resource_per_classic_lb_with_attributes_and_health_merged_in(self):
        w = MagicMock()
        classic_lb = {'LoadBalancerName': 'clb-1', 'VPCId': 'vpc-1', 'Instances': [{'InstanceId': 'i-1'}]}
        client_fn = _clients(classic_lbs=[classic_lb], classic_attrs={'CrossZoneLoadBalancing': {'Enabled': True}},
                              classic_health=[{'InstanceId': 'i-1', 'State': 'InService'}])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        clb_call = calls['classic_load_balancer']
        assert clb_call.kwargs['resource_id'] == 'clb-1'
        assert clb_call.kwargs['scope_id'] == 'vpc-1'
        assert clb_call.kwargs['raw']['_Attributes'] == {'CrossZoneLoadBalancing': {'Enabled': True}}
        assert clb_call.kwargs['raw']['_InstanceHealth'] == [{'InstanceId': 'i-1', 'State': 'InService'}]

    def test_a_classic_lb_with_no_instances_skips_the_instance_health_call(self):
        w = MagicMock()
        classic_lb = {'LoadBalancerName': 'clb-1'}
        client_fn = _clients(classic_lbs=[classic_lb])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['classic_load_balancer'].kwargs['raw']['_InstanceHealth'] == []

    def test_classic_lb_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        classic_lb = {'LoadBalancerName': 'clb-1'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client_fn = _clients(classic_lbs=[classic_lb], classic_tags_by_name={'clb-1': tags})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['classic_load_balancer'].kwargs['tags'] == tags

    def test_a_classic_lbs_failure_does_not_prevent_modern_lbs_from_being_gathered(self):
        w = MagicMock()
        modern_lb = {'LoadBalancerName': 'alb-1', 'LoadBalancerArn': 'arn:alb-1', 'Type': 'network'}
        client_fn = _clients(classic_raise=True, modern_lbs=[modern_lb])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lb (classic)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'load_balancer' in calls

    def test_adds_one_resource_per_modern_lb_with_attributes_listeners_and_target_groups_merged_in(self):
        w = MagicMock()
        modern_lb = {'LoadBalancerName': 'nlb-1', 'LoadBalancerArn': 'arn:nlb-1', 'VpcId': 'vpc-1', 'Type': 'network'}
        tg = {'TargetGroupArn': 'arn:tg-1', 'TargetGroupName': 'tg-1'}
        client_fn = _clients(
            modern_lbs=[modern_lb],
            lb_attrs={'arn:nlb-1': {'deletion_protection.enabled': 'false'}},
            listeners_by_arn={'arn:nlb-1': [{'Protocol': 'TCP'}]},
            target_groups_by_arn={'arn:nlb-1': [tg]},
            tg_attrs_by_arn={'arn:tg-1': {'deregistration_delay.timeout_seconds': '300'}},
            target_health_by_arn={'arn:tg-1': [{'Target': {'Id': 'i-1'}}]},
        )
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        lb_call = calls['load_balancer']
        assert lb_call.kwargs['resource_id'] == 'arn:nlb-1'
        assert lb_call.kwargs['scope_id'] == 'vpc-1'
        assert lb_call.kwargs['raw']['_Attributes'] == {'deletion_protection.enabled': 'false'}
        assert lb_call.kwargs['raw']['_Listeners'] == [{'Protocol': 'TCP'}]
        tg_record = lb_call.kwargs['raw']['_TargetGroups'][0]
        assert tg_record['_Attributes'] == {'deregistration_delay.timeout_seconds': '300'}
        assert tg_record['_TargetHealthDescriptions'] == [{'Target': {'Id': 'i-1'}}]
        assert '_WebACL' not in lb_call.kwargs['raw']

    def test_modern_lb_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        modern_lb = {'LoadBalancerName': 'nlb-1', 'LoadBalancerArn': 'arn:nlb-1', 'Type': 'network'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client_fn = _clients(modern_lbs=[modern_lb], elbv2_tags_by_arn={'arn:nlb-1': tags})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['load_balancer'].kwargs['tags'] == tags

    def test_target_group_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tg = {'TargetGroupArn': 'arn:tg-1', 'TargetGroupName': 'tg-1'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client_fn = _clients(all_target_groups=[tg], elbv2_tags_by_arn={'arn:tg-1': tags})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['target_group'].kwargs['tags'] == tags

    def test_an_application_lb_gets_a_web_acl_lookup(self):
        w = MagicMock()
        alb = {'LoadBalancerName': 'alb-1', 'LoadBalancerArn': 'arn:alb-1', 'Type': 'application'}
        client_fn = _clients(modern_lbs=[alb], web_acl_by_arn={'arn:alb-1': {'Name': 'my-acl'}})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['load_balancer'].kwargs['raw']['_WebACL'] == {'Name': 'my-acl'}

    def test_no_web_acl_attached_is_not_an_error(self):
        w = MagicMock()
        alb = {'LoadBalancerName': 'alb-1', 'LoadBalancerArn': 'arn:alb-1', 'Type': 'application'}
        client_fn = _clients(modern_lbs=[alb], web_acl_not_found_arns={'arn:alb-1'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        w.add_error.assert_not_called()
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['load_balancer'].kwargs['raw']['_WebACL'] is None

    def test_a_modern_lbs_failure_does_not_prevent_classic_lbs_from_being_gathered(self):
        w = MagicMock()
        classic_lb = {'LoadBalancerName': 'clb-1'}
        client_fn = _clients(classic_lbs=[classic_lb], modern_raise=True)
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lb (modern)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'classic_load_balancer' in calls

    def test_the_original_modern_lb_dict_is_not_mutated(self):
        w = MagicMock()
        modern_lb = {'LoadBalancerName': 'nlb-1', 'LoadBalancerArn': 'arn:nlb-1', 'Type': 'network'}
        client_fn = _clients(modern_lbs=[modern_lb])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert '_Attributes' not in modern_lb

    def test_adds_one_resource_per_target_group_region_wide_with_attributes_and_health_merged_in(self):
        w = MagicMock()
        tg = {'TargetGroupArn': 'arn:tg-1', 'TargetGroupName': 'tg-1', 'LoadBalancerArns': []}
        client_fn = _clients(
            all_target_groups=[tg],
            tg_attrs_by_arn={'arn:tg-1': {'deregistration_delay.timeout_seconds': '0'}},
            target_health_by_arn={'arn:tg-1': [{'Target': {'Id': 'i-1'}}]},
        )
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        tg_call = calls['target_group']
        assert tg_call.kwargs['resource_id'] == 'arn:tg-1'
        assert tg_call.kwargs['resource_name'] == 'tg-1'
        assert tg_call.kwargs['raw']['_Attributes'] == {'deregistration_delay.timeout_seconds': '0'}
        assert tg_call.kwargs['raw']['_TargetHealthDescriptions'] == [{'Target': {'Id': 'i-1'}}]

    def test_an_orphaned_target_group_not_attached_to_any_load_balancer_is_still_gathered(self):
        w = MagicMock()
        tg = {'TargetGroupArn': 'arn:tg-orphan', 'TargetGroupName': 'orphan', 'LoadBalancerArns': []}
        client_fn = _clients(all_target_groups=[tg])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['target_group'].kwargs['resource_id'] == 'arn:tg-orphan'

    def test_a_target_groups_failure_does_not_prevent_load_balancers_from_being_gathered(self):
        w = MagicMock()
        modern_lb = {'LoadBalancerName': 'nlb-1', 'LoadBalancerArn': 'arn:nlb-1', 'Type': 'network'}
        client_fn = _clients(modern_lbs=[modern_lb], all_target_groups_raise=True)
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'lb (target groups)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'load_balancer' in calls
        assert 'target_group' not in calls

    def test_a_load_balancers_failure_does_not_prevent_target_groups_from_being_gathered(self):
        w = MagicMock()
        tg = {'TargetGroupArn': 'arn:tg-1', 'TargetGroupName': 'tg-1'}
        client_fn = _clients(modern_raise=True, all_target_groups=[tg])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'target_group' in calls
