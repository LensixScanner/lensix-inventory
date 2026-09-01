"""Unit tests for lensix_inventory.aws.autoscaling — Auto Scaling groups."""

from unittest.mock import MagicMock, patch

import pytest

import lensix_inventory.aws.autoscaling as m


def _asg_client(asgs=None, launch_configs=None, scheduled_actions=None):
    client = MagicMock()

    def _paginator(op, **kw):
        p = MagicMock()
        if op == 'describe_auto_scaling_groups':
            p.paginate.return_value = [{'AutoScalingGroups': asgs or []}]
        elif op == 'describe_scheduled_actions':
            p.paginate.return_value = [{'ScheduledUpdateGroupActions': scheduled_actions or []}]
        return p
    client.get_paginator.side_effect = _paginator
    client.describe_launch_configurations.return_value = {'LaunchConfigurations': launch_configs or []}
    return client


def _ec2_client(lt_versions=None):
    client = MagicMock()
    client.describe_launch_template_versions.return_value = {'LaunchTemplateVersions': lt_versions or []}
    return client


def _clients(asg_client=None, ec2_client=None):
    """boto3.client side_effect dispatching on service name — _launches_
    with_public_ip builds its own client per call (see its own docstring),
    so every test that reaches it needs this rather than a single
    return_value."""
    def _client(service, **kwargs):
        return {'autoscaling': asg_client or _asg_client(), 'ec2': ec2_client or _ec2_client()}[service]
    return _client


class TestGather:
    def test_adds_one_resource_per_group(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:aws:autoscaling:us-east-1:1:asg:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', return_value=_asg_client([asg])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='autoscaling_group', region='us-east-1',
            resource_id='arn:aws:autoscaling:us-east-1:1:asg:1', resource_name='web-asg', raw=asg,
            tags=None,
        )

    def test_group_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg', 'Tags': tags}
        with patch.object(m.boto3, 'client', return_value=_asg_client([asg])):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_no_groups_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_asg_client([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()

    def test_a_group_with_no_launch_template_or_configuration_needs_no_extra_call(self):
        # get_asgs() itself is the only boto3.client call needed — no
        # 'ec2' entry in this dict, so a stray describe_launch_template_
        # versions call would raise KeyError and fail the test.
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg])}[service]):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplatePublicIp'] is None

    def test_attaches_launch_template_public_ip_result_to_the_raw_record(self):
        w = MagicMock()
        asg = {
            'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg',
            'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'},
        }
        lt_version = {'LaunchTemplateData': {'NetworkInterfaces': [{'AssociatePublicIpAddress': True}]}}
        with patch.object(m.boto3, 'client', side_effect=_clients(_asg_client([asg]), _ec2_client([lt_version]))):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplatePublicIp'] is True

    def test_a_lookup_failure_is_recorded_as_a_gather_error_not_swallowed(self):
        # Regression test: an earlier version of _launches_with_public_ip
        # caught this exception internally and returned None with zero
        # trace anywhere — indistinguishable from a legitimate "no launch
        # template" ASG. It must now surface via writer.add_error (->
        # scan_errors) while still not aborting the rest of the region.
        w = MagicMock()
        asg = {
            'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg',
            'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '2'},
        }
        ec2_client = MagicMock()
        ec2_client.describe_launch_template_versions.side_effect = RuntimeError('AccessDenied')
        with patch.object(m.boto3, 'client', side_effect=_clients(_asg_client([asg]), ec2_client)):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplatePublicIp'] is None
        assert asg['_LaunchTemplateIamRole'] is None
        assert asg['_LaunchTemplateUserdataSecretHits'] == []
        assert asg['_LaunchTemplateMarketType'] is None
        # All four independent lookups hit the same broken launch template
        # version, so all four record their own error.
        assert w.add_error.call_count == 4
        for args in (c[0] for c in w.add_error.call_args_list):
            assert args[0] == 'us-east-1'
            assert 'web-asg' in args[2] and 'AccessDenied' in args[2]
        w.add_resource.assert_called_once()  # still gathered despite the lookup failure

    def test_attaches_launch_template_iam_role_result_to_the_raw_record(self):
        w = MagicMock()
        asg = {
            'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg',
            'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'},
        }
        lt_version = {'LaunchTemplateData': {'IamInstanceProfile': {'Arn': 'arn:aws:iam::1:instance-profile/role'}}}
        with patch.object(m.boto3, 'client', side_effect=_clients(_asg_client([asg]), _ec2_client([lt_version]))):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateIamRole'] == {'Arn': 'arn:aws:iam::1:instance-profile/role'}

    def test_no_launch_template_means_no_iam_role_possible(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg])}[service]):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateIamRole'] is None

    def test_attaches_launch_template_userdata_secret_hits_to_the_raw_record(self):
        import base64
        w = MagicMock()
        asg = {
            'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg',
            'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'},
        }
        encoded = base64.b64encode(b'aws_secret_access_key=AKIA_SOMETHING_SECRET').decode()
        lt_version = {'LaunchTemplateData': {'UserData': encoded}}
        with patch.object(m.boto3, 'client', side_effect=_clients(_asg_client([asg]), _ec2_client([lt_version]))):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateUserdataSecretHits'] == ['AWS Secret Access Key']

    def test_no_launch_template_means_no_userdata_secret_hits(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg])}[service]):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateUserdataSecretHits'] == []

    def test_attaches_launch_template_market_type_result_to_the_raw_record(self):
        w = MagicMock()
        asg = {
            'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg',
            'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'},
        }
        lt_version = {'LaunchTemplateData': {'InstanceMarketOptions': {'MarketType': 'spot'}}}
        with patch.object(m.boto3, 'client', side_effect=_clients(_asg_client([asg]), _ec2_client([lt_version]))):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateMarketType'] == 'spot'

    def test_no_launch_template_means_market_type_is_none(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg])}[service]):
            m.gather('us-east-1', w)
        assert asg['_LaunchTemplateMarketType'] is None

    def test_has_scheduled_action_true_when_one_targets_this_asg(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        action = {'AutoScalingGroupName': 'web-asg', 'ScheduledActionName': 'scale-up-morning'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg], scheduled_actions=[action])}[service]):
            m.gather('us-east-1', w)
        assert asg['_HasScheduledAction'] is True

    def test_has_scheduled_action_false_when_none_target_this_asg(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        other_action = {'AutoScalingGroupName': 'other-asg', 'ScheduledActionName': 'scale-up-morning'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg], scheduled_actions=[other_action])}[service]):
            m.gather('us-east-1', w)
        assert asg['_HasScheduledAction'] is False

    def test_has_scheduled_action_false_with_no_scheduled_actions_at_all(self):
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': _asg_client([asg])}[service]):
            m.gather('us-east-1', w)
        assert asg['_HasScheduledAction'] is False

    def test_has_scheduled_action_none_when_the_region_wide_lookup_fails(self):
        # Region-wide failure means "unknown," not "no schedule" — every
        # ASG in the region gets None, and a lookup failure here must not
        # abort the whole region's gather (still gathers the ASG).
        w = MagicMock()
        asg = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'web-asg'}
        asg_client = MagicMock()

        def _paginator(op, **kw):
            if op == 'describe_scheduled_actions':
                raise RuntimeError('AccessDenied')
            p = MagicMock()
            p.paginate.return_value = [{'AutoScalingGroups': [asg]}]
            return p
        asg_client.get_paginator.side_effect = _paginator
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': asg_client}[service]):
            m.gather('us-east-1', w)
        assert asg['_HasScheduledAction'] is None
        w.add_resource.assert_called_once()
        assert any('scheduled actions' in c[0][1] for c in w.add_error.call_args_list)

    def test_scheduled_actions_lookup_is_one_call_for_the_whole_region_not_per_asg(self):
        w = MagicMock()
        asg1 = {'AutoScalingGroupARN': 'arn:1', 'AutoScalingGroupName': 'asg-1'}
        asg2 = {'AutoScalingGroupARN': 'arn:2', 'AutoScalingGroupName': 'asg-2'}
        asg_client = _asg_client([asg1, asg2])
        with patch.object(m.boto3, 'client', side_effect=lambda service, **kw: {'autoscaling': asg_client}[service]):
            m.gather('us-east-1', w)
        scheduled_calls = [c for c in asg_client.get_paginator.call_args_list if c.args[0] == 'describe_scheduled_actions']
        assert len(scheduled_calls) == 1


class TestGetScheduledActions:
    def test_returns_actions_from_every_page(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {'ScheduledUpdateGroupActions': [{'ScheduledActionName': 'a'}]},
            {'ScheduledUpdateGroupActions': [{'ScheduledActionName': 'b'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            actions = m.get_scheduled_actions('us-east-1')
        assert [a['ScheduledActionName'] for a in actions] == ['a', 'b']

    def test_no_scheduled_actions_returns_empty_list(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{'ScheduledUpdateGroupActions': []}]
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_scheduled_actions('us-east-1') == []

    def test_lookup_error_propagates(self):
        client = MagicMock()
        client.get_paginator.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client), pytest.raises(RuntimeError, match='boom'):
            m.get_scheduled_actions('us-east-1')


class TestLaunchTemplateSpec:
    def test_reads_the_top_level_launch_template(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '3'}}
        assert m._launch_template_spec(asg) == {'LaunchTemplateId': 'lt-1', 'Version': '3'}

    def test_reads_the_mixed_instances_policy_launch_template(self):
        asg = {'MixedInstancesPolicy': {'LaunchTemplate': {'LaunchTemplateSpecification': {'LaunchTemplateId': 'lt-2', 'Version': '$Default'}}}}
        assert m._launch_template_spec(asg) == {'LaunchTemplateId': 'lt-2', 'Version': '$Default'}

    def test_none_for_a_launch_configuration_based_group(self):
        assert m._launch_template_spec({'LaunchConfigurationName': 'lc-1'}) is None


class TestNetworkInterfacesAssociatePublicIp:
    def test_true_when_any_interface_explicitly_enables_it(self):
        nis = [{'AssociatePublicIpAddress': False}, {'AssociatePublicIpAddress': True}]
        assert m._network_interfaces_associate_public_ip(nis) is True

    def test_false_when_all_explicit_interfaces_disable_it(self):
        assert m._network_interfaces_associate_public_ip([{'AssociatePublicIpAddress': False}]) is False

    def test_none_when_no_interface_sets_the_field(self):
        assert m._network_interfaces_associate_public_ip([{'DeviceIndex': 0}]) is None

    def test_none_for_no_interfaces_at_all(self):
        assert m._network_interfaces_associate_public_ip([]) is None
        assert m._network_interfaces_associate_public_ip(None) is None


class TestLaunchesWithPublicIp:
    def test_launch_configuration_true(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = _asg_client(launch_configs=[{'AssociatePublicIpAddress': True}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launches_with_public_ip(asg, 'us-east-1') is True

    def test_launch_configuration_missing_returns_none(self):
        asg = {'LaunchConfigurationName': 'lc-gone'}
        with patch.object(m.boto3, 'client', return_value=_asg_client(launch_configs=[])):
            assert m._launches_with_public_ip(asg, 'us-east-1') is None

    def test_launch_configuration_lookup_error_propagates(self):
        # Raises rather than swallowing — gather() is the error boundary
        # (see its own test in TestGather), not this function; a silently
        # eaten exception here would be indistinguishable from a
        # legitimate "no launch configuration" ASG.
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = MagicMock()
        asg_client.describe_launch_configurations.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=asg_client), pytest.raises(RuntimeError, match='boom'):
            m._launches_with_public_ip(asg, 'us-east-1')

    def test_launch_template_by_id_true(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {'NetworkInterfaces': [{'AssociatePublicIpAddress': True}]}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launches_with_public_ip(asg, 'us-east-1') is True
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['$Latest'], LaunchTemplateId='lt-1',
        )

    def test_launch_template_by_name_when_no_id(self):
        asg = {'LaunchTemplate': {'LaunchTemplateName': 'my-lt', 'Version': '2'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {'NetworkInterfaces': []}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            m._launches_with_public_ip(asg, 'us-east-1')
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['2'], LaunchTemplateName='my-lt',
        )

    def test_launch_template_version_not_found_returns_none(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        with patch.object(m.boto3, 'client', return_value=_ec2_client([])):
            assert m._launches_with_public_ip(asg, 'us-east-1') is None

    def test_launch_template_lookup_error_propagates(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = MagicMock()
        ec2_client.describe_launch_template_versions.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ec2_client), pytest.raises(RuntimeError, match='boom'):
            m._launches_with_public_ip(asg, 'us-east-1')

    def test_no_launch_template_or_configuration_returns_none(self):
        assert m._launches_with_public_ip({}, 'us-east-1') is None


class TestLaunchTemplateIamRole:
    def test_launch_configuration_role(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = _asg_client(launch_configs=[{'IamInstanceProfile': 'my-profile'}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launch_template_iam_role(asg, 'us-east-1') == 'my-profile'

    def test_launch_configuration_missing_returns_none(self):
        asg = {'LaunchConfigurationName': 'lc-gone'}
        with patch.object(m.boto3, 'client', return_value=_asg_client(launch_configs=[])):
            assert m._launch_template_iam_role(asg, 'us-east-1') is None

    def test_launch_configuration_lookup_error_propagates(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = MagicMock()
        asg_client.describe_launch_configurations.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=asg_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_iam_role(asg, 'us-east-1')

    def test_launch_template_by_id_role(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {'IamInstanceProfile': {'Arn': 'arn:aws:iam::1:instance-profile/role'}}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_iam_role(asg, 'us-east-1') == {'Arn': 'arn:aws:iam::1:instance-profile/role'}
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['$Latest'], LaunchTemplateId='lt-1',
        )

    def test_launch_template_by_name_when_no_id(self):
        asg = {'LaunchTemplate': {'LaunchTemplateName': 'my-lt', 'Version': '2'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_iam_role(asg, 'us-east-1') is None
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['2'], LaunchTemplateName='my-lt',
        )

    def test_launch_template_version_not_found_returns_none(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        with patch.object(m.boto3, 'client', return_value=_ec2_client([])):
            assert m._launch_template_iam_role(asg, 'us-east-1') is None

    def test_launch_template_lookup_error_propagates(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = MagicMock()
        ec2_client.describe_launch_template_versions.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ec2_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_iam_role(asg, 'us-east-1')

    def test_no_launch_template_or_configuration_returns_none(self):
        assert m._launch_template_iam_role({}, 'us-east-1') is None


class TestLaunchTemplateMarketType:
    def test_launch_configuration_with_spot_price_is_spot(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = _asg_client(launch_configs=[{'SpotPrice': '0.05'}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launch_template_market_type(asg, 'us-east-1') == 'spot'

    def test_launch_configuration_without_spot_price_is_on_demand(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = _asg_client(launch_configs=[{}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launch_template_market_type(asg, 'us-east-1') == 'on-demand'

    def test_launch_configuration_missing_returns_none(self):
        asg = {'LaunchConfigurationName': 'lc-gone'}
        with patch.object(m.boto3, 'client', return_value=_asg_client(launch_configs=[])):
            assert m._launch_template_market_type(asg, 'us-east-1') is None

    def test_launch_configuration_lookup_error_propagates(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = MagicMock()
        asg_client.describe_launch_configurations.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=asg_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_market_type(asg, 'us-east-1')

    def test_launch_template_with_spot_market_type_is_spot(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {'InstanceMarketOptions': {'MarketType': 'spot'}}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_market_type(asg, 'us-east-1') == 'spot'
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['$Latest'], LaunchTemplateId='lt-1',
        )

    def test_launch_template_with_no_market_options_is_on_demand(self):
        asg = {'LaunchTemplate': {'LaunchTemplateName': 'my-lt', 'Version': '2'}}
        ec2_client = _ec2_client([{'LaunchTemplateData': {}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_market_type(asg, 'us-east-1') == 'on-demand'
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['2'], LaunchTemplateName='my-lt',
        )

    def test_launch_template_version_not_found_returns_none(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        with patch.object(m.boto3, 'client', return_value=_ec2_client([])):
            assert m._launch_template_market_type(asg, 'us-east-1') is None

    def test_launch_template_lookup_error_propagates(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = MagicMock()
        ec2_client.describe_launch_template_versions.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ec2_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_market_type(asg, 'us-east-1')

    def test_no_launch_template_or_configuration_returns_none(self):
        assert m._launch_template_market_type({}, 'us-east-1') is None


class TestLaunchTemplateUserdataSecretHits:
    def _b64(self, text):
        import base64
        return base64.b64encode(text.encode()).decode()

    def test_launch_configuration_userdata_scanned(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        encoded = self._b64('aws_secret_access_key=AKIA_SOMETHING_SECRET')
        asg_client = _asg_client(launch_configs=[{'UserData': encoded}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == ['AWS Secret Access Key']

    def test_launch_configuration_no_userdata_returns_empty_list(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = _asg_client(launch_configs=[{}])
        with patch.object(m.boto3, 'client', return_value=asg_client):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == []

    def test_launch_configuration_missing_returns_empty_list(self):
        asg = {'LaunchConfigurationName': 'lc-gone'}
        with patch.object(m.boto3, 'client', return_value=_asg_client(launch_configs=[])):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == []

    def test_launch_configuration_lookup_error_propagates(self):
        asg = {'LaunchConfigurationName': 'lc-1'}
        asg_client = MagicMock()
        asg_client.describe_launch_configurations.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=asg_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_userdata_secret_hits(asg, 'us-east-1')

    def test_launch_template_userdata_scanned(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        encoded = self._b64('aws_secret_access_key=AKIA_SOMETHING_SECRET')
        ec2_client = _ec2_client([{'LaunchTemplateData': {'UserData': encoded}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == ['AWS Secret Access Key']
        ec2_client.describe_launch_template_versions.assert_called_once_with(
            Versions=['$Latest'], LaunchTemplateId='lt-1',
        )

    def test_launch_template_no_secrets_returns_empty_list(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        encoded = self._b64('echo hello world')
        ec2_client = _ec2_client([{'LaunchTemplateData': {'UserData': encoded}}])
        with patch.object(m.boto3, 'client', return_value=ec2_client):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == []

    def test_launch_template_version_not_found_returns_empty_list(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        with patch.object(m.boto3, 'client', return_value=_ec2_client([])):
            assert m._launch_template_userdata_secret_hits(asg, 'us-east-1') == []

    def test_launch_template_lookup_error_propagates(self):
        asg = {'LaunchTemplate': {'LaunchTemplateId': 'lt-1', 'Version': '$Latest'}}
        ec2_client = MagicMock()
        ec2_client.describe_launch_template_versions.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ec2_client), pytest.raises(RuntimeError, match='boom'):
            m._launch_template_userdata_secret_hits(asg, 'us-east-1')

    def test_no_launch_template_or_configuration_returns_empty_list(self):
        assert m._launch_template_userdata_secret_hits({}, 'us-east-1') == []
