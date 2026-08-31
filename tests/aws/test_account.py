"""Unit tests for lensix_inventory.aws.account — IAM/SSO (global) and
KMS/CloudTrail/Config/GuardDuty/Access-Analyzer/CloudWatch-Logs/X-Ray
(regional) account-level resources. The largest gather module in this
tool; organized into helper-function tests (one class per get_*()
fetcher) followed by orchestration tests for gather_global()/gather()."""

import json
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

import lensix_inventory.aws.account as m


def _client_error(code):
    return botocore.exceptions.ClientError({'Error': {'Code': code}}, 'SomeOperation')


# --- _try ---------------------------------------------------------------------

class TestTry:
    def test_returns_the_result_on_success(self):
        assert m._try(lambda: {'ok': True}) == {'ok': True}

    def test_a_client_error_returns_the_error_code(self):
        def _raise():
            raise _client_error('NoSuchEntity')
        assert m._try(_raise) == {'_error': 'NoSuchEntity'}

    def test_a_generic_exception_is_also_captured(self):
        def _raise():
            raise RuntimeError('boom')
        assert m._try(_raise) == {'_error': 'boom'}


# --- Global (IAM/SSO) fetchers -------------------------------------------------

class TestGetIamRoles:
    def test_paginates_and_returns_roles(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [{'Roles': [{'RoleName': 'r1'}]}]
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_iam_roles() == [{'RoleName': 'r1'}]


class TestGetIamGroups:
    def test_merges_members_and_policies_per_group(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [{'Groups': [{'GroupName': 'admins'}]}]
        iam.get_group.return_value = {'Users': [{'UserName': 'alice'}]}
        iam.list_group_policies.return_value = {'PolicyNames': ['inline1']}
        iam.list_attached_group_policies.return_value = {'AttachedPolicies': [{'PolicyName': 'AdministratorAccess'}]}
        with patch.object(m.boto3, 'client', return_value=iam):
            groups = m.get_iam_groups()
        assert groups[0]['_Users'] == [{'UserName': 'alice'}]
        assert groups[0]['_InlinePolicyNames'] == ['inline1']
        assert groups[0]['_AttachedPolicies'] == [{'PolicyName': 'AdministratorAccess'}]

    def test_a_fanout_failure_for_one_group_falls_back_to_empty_lists(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [{'Groups': [{'GroupName': 'admins'}]}]
        iam.get_group.side_effect = RuntimeError('boom')
        iam.list_group_policies.side_effect = RuntimeError('boom')
        iam.list_attached_group_policies.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=iam):
            groups = m.get_iam_groups()
        assert groups[0]['_Users'] == []
        assert groups[0]['_InlinePolicyNames'] == []
        assert groups[0]['_AttachedPolicies'] == []


class TestGetIamPolicies:
    def test_merges_the_policy_document(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [
            {'Policies': [{'Arn': 'arn:1', 'DefaultVersionId': 'v1'}]}
        ]
        iam.get_policy_version.return_value = {'PolicyVersion': {'Document': {'Statement': []}}}
        with patch.object(m.boto3, 'client', return_value=iam):
            policies = m.get_iam_policies()
        assert policies[0]['_PolicyDocument'] == {'Statement': []}
        iam.get_paginator.assert_called_with('list_policies')
        assert iam.get_paginator.return_value.paginate.call_args.kwargs == {'Scope': 'Local'}

    def test_a_policy_version_failure_leaves_the_document_none(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [{'Policies': [{'Arn': 'arn:1', 'DefaultVersionId': 'v1'}]}]
        iam.get_policy_version.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=iam):
            policies = m.get_iam_policies()
        assert policies[0]['_PolicyDocument'] is None


class TestGetIamServerCertificates:
    def test_paginates_and_returns_certificates(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [{'ServerCertificateMetadataList': [{'ServerCertificateName': 'c1'}]}]
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_iam_server_certificates() == [{'ServerCertificateName': 'c1'}]


class TestGetIamVirtualMfaDevices:
    def test_returns_assigned_devices(self):
        iam = MagicMock()
        iam.list_virtual_mfa_devices.return_value = {'VirtualMFADevices': [{'SerialNumber': 'arn:1'}]}
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_iam_virtual_mfa_devices() == [{'SerialNumber': 'arn:1'}]

    def test_returns_empty_list_on_failure(self):
        iam = MagicMock()
        iam.list_virtual_mfa_devices.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_iam_virtual_mfa_devices() == []


class TestGetPasswordPolicy:
    def test_configured_policy(self):
        iam = MagicMock()
        iam.get_account_password_policy.return_value = {'PasswordPolicy': {'MinimumPasswordLength': 14}}
        with patch.object(m.boto3, 'client', return_value=iam):
            policy = m.get_password_policy()
        assert policy == {'_configured': True, 'MinimumPasswordLength': 14}

    def test_no_policy_configured(self):
        iam = MagicMock()

        class _NoSuchEntity(Exception):
            pass
        iam.exceptions.NoSuchEntityException = _NoSuchEntity
        iam.get_account_password_policy.side_effect = _NoSuchEntity()
        with patch.object(m.boto3, 'client', return_value=iam):
            policy = m.get_password_policy()
        assert policy == {'_configured': False}

    def test_an_unexpected_error_is_recorded(self):
        iam = MagicMock()

        class _NoSuchEntity(Exception):
            pass
        iam.exceptions.NoSuchEntityException = _NoSuchEntity
        iam.get_account_password_policy.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=iam):
            policy = m.get_password_policy()
        assert policy['_configured'] is None
        assert policy['_error'] == 'boom'


class TestGetAccountSummary:
    def test_returns_the_summary_map(self):
        iam = MagicMock()
        iam.get_account_summary.return_value = {'SummaryMap': {'Users': 5}}
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_account_summary() == {'Users': 5}


class TestGetSupportAccessRoles:
    def test_returns_roles_with_the_policy_attached(self):
        iam = MagicMock()
        iam.list_entities_for_policy.return_value = {'PolicyRoles': [{'RoleName': 'support'}]}
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_support_access_roles() == [{'RoleName': 'support'}]

    def test_returns_empty_list_on_failure(self):
        iam = MagicMock()
        iam.list_entities_for_policy.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=iam):
            assert m.get_support_access_roles() == []


class TestGetSsoInstances:
    def test_returns_instances(self):
        sso = MagicMock()
        sso.list_instances.return_value = {'Instances': [{'InstanceArn': 'arn:1'}]}
        with patch.object(m.boto3, 'client', return_value=sso):
            assert m.get_sso_instances() == [{'InstanceArn': 'arn:1'}]

    def test_returns_empty_list_when_sso_is_not_enabled(self):
        sso = MagicMock()
        sso.list_instances.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=sso):
            assert m.get_sso_instances() == []


class TestGetSsoPermissionSets:
    def test_describes_each_permission_set(self):
        sso = MagicMock()
        sso.get_paginator.return_value.paginate.return_value = [{'PermissionSets': ['arn:ps1']}]
        sso.describe_permission_set.return_value = {'PermissionSet': {'Name': 'AdminAccess'}}
        with patch.object(m.boto3, 'client', return_value=sso):
            sets = m.get_sso_permission_sets('arn:instance')
        assert sets == [{'Name': 'AdminAccess'}]

    def test_a_describe_failure_for_one_permission_set_does_not_abort_the_others(self):
        sso = MagicMock()
        sso.get_paginator.return_value.paginate.return_value = [{'PermissionSets': ['bad', 'good']}]
        sso.describe_permission_set.side_effect = [RuntimeError('boom'), {'PermissionSet': {'Name': 'good'}}]
        with patch.object(m.boto3, 'client', return_value=sso):
            sets = m.get_sso_permission_sets('arn:instance')
        assert sets == [{'Name': 'good'}]


class TestGetSsoTags:
    def test_paginates_across_next_token(self):
        sso = MagicMock()
        sso.list_tags_for_resource.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'NextToken': 'page2'},
            {'Tags': [{'Key': 'b', 'Value': '2'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=sso):
            tags = m.get_sso_tags('arn:instance', 'arn:resource')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]
        assert sso.list_tags_for_resource.call_args_list[0].kwargs == {'InstanceArn': 'arn:instance', 'ResourceArn': 'arn:resource'}

    def test_returns_empty_list_on_failure(self):
        sso = MagicMock()
        sso.list_tags_for_resource.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=sso):
            assert m.get_sso_tags('arn:instance', 'arn:resource') == []


# --- Regional fetchers ----------------------------------------------------------

class TestGetKmsKeys:
    def test_merges_rotation_status_aliases_and_policy(self):
        kms = MagicMock()
        kms.get_paginator.return_value.paginate.return_value = [{'Keys': [{'KeyId': 'k1'}]}]
        kms.describe_key.return_value = {'KeyMetadata': {'KeyId': 'k1', 'KeyManager': 'CUSTOMER', 'KeyState': 'Enabled'}}
        kms.get_key_rotation_status.return_value = {'KeyRotationEnabled': True}
        kms.list_aliases.return_value = {'Aliases': [{'AliasName': 'alias/my-key'}]}
        kms.get_key_policy.return_value = {'Policy': json.dumps({'Statement': []})}
        with patch.object(m.boto3, 'client', return_value=kms):
            keys = m.get_kms_keys('us-east-1')
        assert keys[0]['_RotationStatus'] == {'KeyRotationEnabled': True}
        assert keys[0]['_Aliases'] == [{'AliasName': 'alias/my-key'}]
        assert keys[0]['_KeyPolicy'] == {'Statement': []}

    def test_aws_managed_keys_are_skipped(self):
        kms = MagicMock()
        kms.get_paginator.return_value.paginate.return_value = [{'Keys': [{'KeyId': 'k1'}]}]
        kms.describe_key.return_value = {'KeyMetadata': {'KeyId': 'k1', 'KeyManager': 'AWS'}}
        with patch.object(m.boto3, 'client', return_value=kms):
            assert m.get_kms_keys('us-east-1') == []

    def test_a_describe_key_failure_skips_that_key(self):
        kms = MagicMock()
        kms.get_paginator.return_value.paginate.return_value = [{'Keys': [{'KeyId': 'bad'}, {'KeyId': 'good'}]}]
        kms.describe_key.side_effect = [RuntimeError('boom'), {'KeyMetadata': {'KeyId': 'good', 'KeyManager': 'CUSTOMER', 'KeyState': 'Enabled'}}]
        kms.get_key_rotation_status.return_value = {}
        kms.list_aliases.return_value = {'Aliases': []}
        kms.get_key_policy.return_value = {}
        with patch.object(m.boto3, 'client', return_value=kms):
            keys = m.get_kms_keys('us-east-1')
        assert [k['KeyId'] for k in keys] == ['good']

    def test_pending_deletion_keys_skip_the_alias_lookup(self):
        kms = MagicMock()
        kms.get_paginator.return_value.paginate.return_value = [{'Keys': [{'KeyId': 'k1'}]}]
        kms.describe_key.return_value = {'KeyMetadata': {'KeyId': 'k1', 'KeyManager': 'CUSTOMER', 'KeyState': 'PendingDeletion'}}
        kms.get_key_rotation_status.return_value = {}
        kms.get_key_policy.return_value = {}
        with patch.object(m.boto3, 'client', return_value=kms):
            keys = m.get_kms_keys('us-east-1')
        assert keys[0]['_Aliases'] == []
        kms.list_aliases.assert_not_called()

    def test_a_malformed_policy_document_leaves_key_policy_none(self):
        kms = MagicMock()
        kms.get_paginator.return_value.paginate.return_value = [{'Keys': [{'KeyId': 'k1'}]}]
        kms.describe_key.return_value = {'KeyMetadata': {'KeyId': 'k1', 'KeyManager': 'CUSTOMER', 'KeyState': 'Enabled'}}
        kms.get_key_rotation_status.return_value = {}
        kms.list_aliases.return_value = {'Aliases': []}
        kms.get_key_policy.return_value = {'Policy': 'not-json'}
        with patch.object(m.boto3, 'client', return_value=kms):
            keys = m.get_kms_keys('us-east-1')
        assert keys[0]['_KeyPolicy'] is None


class TestGetKmsKeyTags:
    def test_paginates_across_truncated_marker(self):
        kms = MagicMock()
        kms.list_resource_tags.side_effect = [
            {'Tags': [{'TagKey': 'a', 'TagValue': '1'}], 'Truncated': True, 'NextMarker': 'm2'},
            {'Tags': [{'TagKey': 'b', 'TagValue': '2'}], 'Truncated': False},
        ]
        with patch.object(m.boto3, 'client', return_value=kms):
            tags = m.get_kms_key_tags('us-east-1', 'k1')
        assert tags == [{'TagKey': 'a', 'TagValue': '1'}, {'TagKey': 'b', 'TagValue': '2'}]

    def test_returns_empty_list_on_failure(self):
        kms = MagicMock()
        kms.list_resource_tags.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=kms):
            assert m.get_kms_key_tags('us-east-1', 'k1') == []


class TestGetCloudtrailTrails:
    def test_merges_event_selectors_status_and_bucket_metadata(self):
        def _client(service, region_name=None):
            if service == 'cloudtrail':
                ct = MagicMock()
                ct.describe_trails.return_value = {'trailList': [{'TrailARN': 'arn:1', 'S3BucketName': 'trail-bucket'}]}
                ct.get_event_selectors.return_value = {'EventSelectors': []}
                ct.get_trail_status.return_value = {'IsLogging': True}
                return ct
            if service == 's3':
                s3 = MagicMock()
                s3.get_bucket_logging.return_value = {}
                s3.get_bucket_policy.return_value = {'Policy': json.dumps({'Statement': []})}
                s3.get_public_access_block.return_value = {}
                return s3
        with patch.object(m.boto3, 'client', side_effect=_client):
            trails = m.get_cloudtrail_trails('us-east-1')
        assert trails[0]['_EventSelectors'] == {'EventSelectors': []}
        assert trails[0]['_TrailStatus'] == {'IsLogging': True}
        assert trails[0]['_BucketPolicy'] == {'Statement': []}

    def test_no_bucket_skips_the_bucket_fanout(self):
        def _client(service, region_name=None):
            if service == 'cloudtrail':
                ct = MagicMock()
                ct.describe_trails.return_value = {'trailList': [{'TrailARN': 'arn:1'}]}
                ct.get_event_selectors.return_value = {}
                ct.get_trail_status.return_value = {}
                return ct
            return MagicMock()
        with patch.object(m.boto3, 'client', side_effect=_client):
            trails = m.get_cloudtrail_trails('us-east-1')
        assert '_BucketLogging' not in trails[0]

    def test_returns_empty_list_when_describe_trails_fails(self):
        ct = MagicMock()
        ct.describe_trails.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ct):
            assert m.get_cloudtrail_trails('us-east-1') == []


class TestGetTrailTags:
    def test_returns_the_matching_trails_own_tags_list(self):
        ct = MagicMock()
        ct.list_tags.return_value = {'ResourceTagList': [
            {'ResourceId': 'arn:1', 'TagsList': [{'Key': 'lensix-suppress', 'Value': 'true'}]},
        ]}
        with patch.object(m.boto3, 'client', return_value=ct):
            tags = m.get_trail_tags('us-east-1', 'arn:1')
        assert tags == [{'Key': 'lensix-suppress', 'Value': 'true'}]
        ct.list_tags.assert_called_once_with(ResourceIdList=['arn:1'])

    def test_returns_empty_list_on_failure(self):
        ct = MagicMock()
        ct.list_tags.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=ct):
            assert m.get_trail_tags('us-east-1', 'arn:1') == []


class TestGetConfigRecorders:
    def test_merges_status_by_name(self):
        cfg = MagicMock()
        cfg.describe_configuration_recorders.return_value = {'ConfigurationRecorders': [{'name': 'default'}]}
        cfg.describe_configuration_recorder_status.return_value = {'ConfigurationRecordersStatus': [{'name': 'default', 'recording': True}]}
        with patch.object(m.boto3, 'client', return_value=cfg):
            recorders = m.get_config_recorders('us-east-1')
        assert recorders[0]['_Status'] == {'name': 'default', 'recording': True}

    def test_a_status_failure_leaves_status_none_but_keeps_the_recorder(self):
        cfg = MagicMock()
        cfg.describe_configuration_recorders.return_value = {'ConfigurationRecorders': [{'name': 'default'}]}
        cfg.describe_configuration_recorder_status.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=cfg):
            recorders = m.get_config_recorders('us-east-1')
        assert recorders[0]['_Status'] is None

    def test_returns_empty_list_when_recorders_fail(self):
        cfg = MagicMock()
        cfg.describe_configuration_recorders.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_recorders('us-east-1') == []


class TestGetConfigDeliveryChannels:
    def test_returns_channels(self):
        cfg = MagicMock()
        cfg.describe_delivery_channel_status.return_value = {'DeliveryChannelsStatus': [{'name': 'default'}]}
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_delivery_channels('us-east-1') == [{'name': 'default'}]

    def test_returns_empty_list_on_failure(self):
        cfg = MagicMock()
        cfg.describe_delivery_channel_status.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_delivery_channels('us-east-1') == []


class TestGetConfigAggregators:
    def test_returns_aggregators(self):
        cfg = MagicMock()
        cfg.describe_configuration_aggregators.return_value = {'ConfigurationAggregators': [{'ConfigurationAggregatorName': 'agg1'}]}
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_aggregators('us-east-1') == [{'ConfigurationAggregatorName': 'agg1'}]

    def test_returns_empty_list_on_failure(self):
        cfg = MagicMock()
        cfg.describe_configuration_aggregators.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_aggregators('us-east-1') == []


class TestGetConfigAggregatorTags:
    def test_paginates_across_next_token(self):
        cfg = MagicMock()
        cfg.list_tags_for_resource.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'NextToken': 'page2'},
            {'Tags': [{'Key': 'b', 'Value': '2'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=cfg):
            tags = m.get_config_aggregator_tags('us-east-1', 'arn:agg1')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]

    def test_returns_empty_list_on_failure(self):
        cfg = MagicMock()
        cfg.list_tags_for_resource.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=cfg):
            assert m.get_config_aggregator_tags('us-east-1', 'arn:agg1') == []


class TestGetGuarddutyDetectors:
    def test_merges_detector_id_into_each_detail(self):
        gd = MagicMock()
        gd.list_detectors.return_value = {'DetectorIds': ['d1']}
        gd.get_detector.return_value = {'Status': 'ENABLED'}
        with patch.object(m.boto3, 'client', return_value=gd):
            detectors = m.get_guardduty_detectors('us-east-1')
        assert detectors == [{'Status': 'ENABLED', 'DetectorId': 'd1'}]

    def test_a_get_detector_failure_skips_that_detector(self):
        gd = MagicMock()
        gd.list_detectors.return_value = {'DetectorIds': ['bad', 'good']}
        gd.get_detector.side_effect = [RuntimeError('boom'), {'Status': 'ENABLED'}]
        with patch.object(m.boto3, 'client', return_value=gd):
            detectors = m.get_guardduty_detectors('us-east-1')
        assert [d['DetectorId'] for d in detectors] == ['good']

    def test_returns_empty_list_when_list_detectors_fails(self):
        gd = MagicMock()
        gd.list_detectors.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=gd):
            assert m.get_guardduty_detectors('us-east-1') == []


class TestGetAccessAnalyzers:
    def test_returns_analyzers(self):
        aa = MagicMock()
        aa.list_analyzers.return_value = {'analyzers': [{'name': 'a1'}]}
        with patch.object(m.boto3, 'client', return_value=aa):
            assert m.get_access_analyzers('us-east-1') == [{'name': 'a1'}]

    def test_returns_empty_list_on_failure(self):
        aa = MagicMock()
        aa.list_analyzers.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=aa):
            assert m.get_access_analyzers('us-east-1') == []


class TestGetLogGroups:
    def test_paginates_and_returns_log_groups(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.return_value = [{'logGroups': [{'logGroupName': '/my/group'}]}]
        with patch.object(m.boto3, 'client', return_value=logs):
            assert m.get_log_groups('us-east-1') == [{'logGroupName': '/my/group'}]

    def test_returns_empty_list_on_failure(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=logs):
            assert m.get_log_groups('us-east-1') == []


class TestGetLogGroupTags:
    def test_returns_the_tags_dict(self):
        logs = MagicMock()
        logs.list_tags_for_resource.return_value = {'tags': {'lensix-suppress': 'true'}}
        with patch.object(m.boto3, 'client', return_value=logs):
            tags = m.get_log_group_tags('us-east-1', 'arn:lg1')
        assert tags == {'lensix-suppress': 'true'}
        logs.list_tags_for_resource.assert_called_once_with(resourceArn='arn:lg1')

    def test_returns_empty_dict_on_failure(self):
        logs = MagicMock()
        logs.list_tags_for_resource.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=logs):
            assert m.get_log_group_tags('us-east-1', 'arn:lg1') == {}


class TestGetXrayEncryptionConfig:
    def test_returns_the_config(self):
        xray = MagicMock()
        xray.get_encryption_config.return_value = {'EncryptionConfig': {'Type': 'KMS'}}
        with patch.object(m.boto3, 'client', return_value=xray):
            assert m.get_xray_encryption_config('us-east-1') == {'Type': 'KMS'}

    def test_returns_none_on_failure(self):
        xray = MagicMock()
        xray.get_encryption_config.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=xray):
            assert m.get_xray_encryption_config('us-east-1') is None


class TestGetTrailLogGroupNames:
    def test_extracts_the_log_group_name_from_the_arn(self):
        trails = [{'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:111111111111:log-group:my-lg:*'}]
        assert m.get_trail_log_group_names(trails) == ['my-lg']

    def test_skips_trails_without_a_log_group(self):
        assert m.get_trail_log_group_names([{}]) == []

    def test_handles_multiple_trails(self):
        trails = [
            {'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:lg1:*'},
            {'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:lg2:*'},
        ]
        assert m.get_trail_log_group_names(trails) == ['lg1', 'lg2']


class TestGetMetricFiltersWithAlarms:
    def test_merges_alarms_for_each_metric_transformation(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.return_value = [{'metricFilters': [
            {'filterName': 'f1', 'metricTransformations': [{'metricName': 'm1', 'metricNamespace': 'ns1'}]}]}]
        cw = MagicMock()
        cw.describe_alarms_for_metric.return_value = {'MetricAlarms': [{'AlarmName': 'a1', 'AlarmActions': ['arn:sns:1']}]}

        def _client(service, **kwargs):
            return {'logs': logs, 'cloudwatch': cw}[service]
        with patch.object(m.boto3, 'client', side_effect=_client):
            result = m.get_metric_filters_with_alarms('us-east-1', 'my-lg')
        assert result[0]['_Alarms'] == [{'AlarmName': 'a1', 'AlarmActions': ['arn:sns:1']}]

    def test_a_filter_with_no_alarms_gets_an_empty_list(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.return_value = [{'metricFilters': [
            {'filterName': 'f1', 'metricTransformations': [{'metricName': 'm1', 'metricNamespace': 'ns1'}]}]}]
        cw = MagicMock()
        cw.describe_alarms_for_metric.return_value = {'MetricAlarms': []}

        def _client(service, **kwargs):
            return {'logs': logs, 'cloudwatch': cw}[service]
        with patch.object(m.boto3, 'client', side_effect=_client):
            result = m.get_metric_filters_with_alarms('us-east-1', 'my-lg')
        assert result[0]['_Alarms'] == []

    def test_an_alarm_lookup_failure_is_swallowed_to_an_empty_list(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.return_value = [{'metricFilters': [
            {'filterName': 'f1', 'metricTransformations': [{'metricName': 'm1', 'metricNamespace': 'ns1'}]}]}]
        cw = MagicMock()
        cw.describe_alarms_for_metric.side_effect = RuntimeError('boom')

        def _client(service, **kwargs):
            return {'logs': logs, 'cloudwatch': cw}[service]
        with patch.object(m.boto3, 'client', side_effect=_client):
            result = m.get_metric_filters_with_alarms('us-east-1', 'my-lg')
        assert result[0]['_Alarms'] == []

    def test_a_filter_with_no_metric_transformations_gets_no_alarms(self):
        logs = MagicMock()
        logs.get_paginator.return_value.paginate.return_value = [{'metricFilters': [
            {'filterName': 'f1', 'metricTransformations': []}]}]
        with patch.object(m.boto3, 'client', return_value=logs):
            result = m.get_metric_filters_with_alarms('us-east-1', 'my-lg')
        assert result[0]['_Alarms'] == []


class TestGetEventbridgeRules:
    def test_merges_targets_for_each_rule(self):
        events = MagicMock()
        events.get_paginator.return_value.paginate.return_value = [{'Rules': [{'Name': 'r1', 'State': 'ENABLED'}]}]
        events.list_targets_by_rule.return_value = {'Targets': [{'Arn': 'arn:1'}]}
        with patch.object(m.boto3, 'client', return_value=events):
            result = m.get_eventbridge_rules('us-east-1')
        assert result[0]['_Targets'] == [{'Arn': 'arn:1'}]

    def test_a_targets_lookup_failure_is_swallowed_to_an_empty_list(self):
        events = MagicMock()
        events.get_paginator.return_value.paginate.return_value = [{'Rules': [{'Name': 'r1', 'State': 'ENABLED'}]}]
        events.list_targets_by_rule.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=events):
            result = m.get_eventbridge_rules('us-east-1')
        assert result[0]['_Targets'] == []

    def test_uses_the_default_event_bus_when_not_specified(self):
        events = MagicMock()
        events.get_paginator.return_value.paginate.return_value = [{'Rules': [{'Name': 'r1'}]}]
        events.list_targets_by_rule.return_value = {'Targets': []}
        with patch.object(m.boto3, 'client', return_value=events):
            m.get_eventbridge_rules('us-east-1')
        assert events.list_targets_by_rule.call_args.kwargs['EventBusName'] == 'default'


class TestGetEventbridgeRuleTags:
    def test_returns_the_tags_list(self):
        events = MagicMock()
        events.list_tags_for_resource.return_value = {'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        with patch.object(m.boto3, 'client', return_value=events):
            tags = m.get_eventbridge_rule_tags('us-east-1', 'arn:rule1')
        assert tags == [{'Key': 'lensix-suppress', 'Value': 'true'}]
        events.list_tags_for_resource.assert_called_once_with(ResourceARN='arn:rule1')

    def test_returns_empty_list_on_failure(self):
        events = MagicMock()
        events.list_tags_for_resource.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=events):
            assert m.get_eventbridge_rule_tags('us-east-1', 'arn:rule1') == []


class _LimitExceeded(Exception):
    pass


class _ReportNotReady(Exception):
    pass


class TestGetRootCredentialReportRow:
    def _iam(self, rows, report_never_ready=False, generate_raises_limit_exceeded=False):
        iam = MagicMock()
        iam.exceptions.LimitExceededException = _LimitExceeded
        iam.exceptions.CredentialReportNotReadyException = _ReportNotReady
        if generate_raises_limit_exceeded:
            iam.generate_credential_report.side_effect = _LimitExceeded()
        if report_never_ready:
            iam.get_credential_report.side_effect = _ReportNotReady()
        else:
            header = sorted({k for row in rows for k in row}) or ['user', 'arn']
            lines = [','.join(header)] + [','.join(row.get(k, '') for k in header) for row in rows]
            content = '\n'.join(lines) + '\n'
            iam.get_credential_report.return_value = {'Content': content.encode('utf-8')}
        return iam

    def test_returns_the_root_row(self):
        rows = [{'user': 'alice', 'arn': 'x'}, {'user': '<root_account>', 'arn': 'arn:root', 'mfa_active': 'true'}]
        with patch.object(m.boto3, 'client', return_value=self._iam(rows)):
            row = m.get_root_credential_report_row()
        assert row['user'] == '<root_account>'
        assert row['mfa_active'] == 'true'

    def test_returns_none_when_no_root_row_present(self):
        rows = [{'user': 'alice', 'arn': 'x'}]
        with patch.object(m.boto3, 'client', return_value=self._iam(rows)):
            assert m.get_root_credential_report_row() is None

    def test_a_report_already_in_progress_falls_through_to_polling(self):
        rows = [{'user': '<root_account>', 'arn': 'arn:root'}]
        with patch.object(m.boto3, 'client', return_value=self._iam(rows, generate_raises_limit_exceeded=True)):
            row = m.get_root_credential_report_row()
        assert row['user'] == '<root_account>'

    def test_raises_after_15_retries_when_never_ready(self):
        with patch.object(m.boto3, 'client', return_value=self._iam([], report_never_ready=True)), patch.object(m.time, 'sleep'):
            try:
                m.get_root_credential_report_row()
                assert False, 'expected TimeoutError'
            except TimeoutError:
                pass


# --- gather_global() orchestration ------------------------------------------

class TestGatherGlobal:
    # gather_global() also calls get_sso_tags()/get_eventbridge_rule_tags()
    # for real (they're new, separate tag-fetch calls, not one of the
    # get_*() list-fetchers every test below already mocks) — an autouse
    # fixture default-patches both to an empty result so every existing
    # test here keeps making no live boto3 call, without editing each
    # one's own `with patch.object(...)` chain individually. Tests that
    # care about tag passthrough specifically override with their own
    # nested patch (see test_sso_*_tags_are_passed_through_for_suppression
    # below).
    @pytest.fixture(autouse=True)
    def _tag_helpers(self):
        with patch.object(m, 'get_sso_tags', return_value=[]), \
             patch.object(m, 'get_eventbridge_rule_tags', return_value=[]):
            yield

    def test_adds_one_resource_per_role_group_policy_cert_and_device(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[{'RoleId': 'r1', 'RoleName': 'my-role'}]), \
             patch.object(m, 'get_iam_groups', return_value=[{'GroupId': 'g1', 'GroupName': 'admins'}]), \
             patch.object(m, 'get_iam_policies', return_value=[{'Arn': 'arn:p1', 'PolicyName': 'my-policy'}]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[{'Arn': 'arn:c1', 'ServerCertificateName': 'cert1'}]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[{'SerialNumber': 'arn:mfa1'}]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': True}), \
             patch.object(m, 'get_account_summary', return_value={'Users': 1}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['iam_role'].kwargs['resource_id'] == 'r1'
        assert calls['iam_group'].kwargs['resource_id'] == 'g1'
        assert calls['iam_policy'].kwargs['resource_id'] == 'arn:p1'
        assert calls['iam_server_certificate'].kwargs['resource_id'] == 'arn:c1'
        assert calls['iam_virtual_mfa_device'].kwargs['resource_id'] == 'arn:mfa1'
        assert calls['iam_password_policy'].kwargs['resource_id'] == 'password_policy'
        assert calls['iam_account_summary'].kwargs['raw']['SummaryMap'] == {'Users': 1}

    def test_the_root_credential_report_row_is_gathered_as_iam_root(self):
        w = MagicMock()
        root_row = {'user': '<root_account>', 'access_key_1_active': 'true'}
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=root_row):
            m.gather_global(w, '123456789012')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        root_call = calls['iam_root']
        assert root_call.kwargs['resource_id'] == 'root'
        assert root_call.kwargs['raw'] == root_row

    def test_a_credential_report_failure_still_gathers_an_empty_iam_root(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', side_effect=RuntimeError('boom')):
            m.gather_global(w, '123456789012')
        assert any(c.kwargs['source'] == 'account (root credential report)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['iam_root'].kwargs['raw'] == {}

    def test_sso_instances_and_permission_sets_are_gathered_with_the_instance_as_scope(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[{'InstanceArn': 'arn:inst1', 'IdentityStoreId': 'd-1'}]), \
             patch.object(m, 'get_sso_permission_sets', return_value=[{'PermissionSetArn': 'arn:ps1', 'Name': 'AdminAccess'}]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['sso_instance'].kwargs['resource_id'] == 'arn:inst1'
        assert calls['sso_permission_set'].kwargs['scope_id'] == 'arn:inst1'

    def test_a_permission_sets_failure_does_not_abort_the_sso_instance_itself(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[{'InstanceArn': 'arn:inst1'}]), \
             patch.object(m, 'get_sso_permission_sets', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        assert any('sso permission sets' in c.kwargs['source'] for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'sso_instance' in calls

    def test_account_summary_failure_skips_that_resource_but_not_the_others(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[{'RoleId': 'r1', 'RoleName': 'r1'}]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        assert any(c.kwargs['source'] == 'account (account summary)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'iam_account_summary' not in calls
        assert 'iam_role' in calls

    def test_every_fetch_is_isolated_from_the_others(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_iam_groups', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_iam_policies', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_iam_server_certificates', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_iam_virtual_mfa_devices', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_password_policy', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_account_summary', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_sso_instances', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_root_credential_report_row', side_effect=RuntimeError('boom')):
            m.gather_global(w, '123456789012')
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert sources == {
            'account (iam roles)', 'account (iam groups)', 'account (iam policies)',
            'account (server certificates)', 'account (virtual mfa devices)',
            'account (password policy)', 'account (account summary)', 'account (sso instances)',
            'account (root credential report)',
        }
        # Password policy and iam_root still get a resource even on total
        # failure — both fall back to a placeholder rather than being
        # skipped ({'_configured': None} and {} respectively).
        calls = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert calls == {'iam_password_policy', 'iam_root'}

    def test_no_regions_gathers_no_root_usage_coverage_data_at_all(self):
        w = MagicMock()
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None), \
             patch.object(m, 'get_cloudtrail_trails') as get_trails:
            m.gather_global(w, '123456789012')
        get_trails.assert_not_called()

    def test_regions_gathers_metric_filters_and_eventbridge_rules_per_region(self):
        w = MagicMock()
        trail = {'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:my-lg:*'}
        mf = {'filterName': 'f1', '_Alarms': []}
        rule = {'Name': 'r1', '_Targets': []}
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_metric_filters_with_alarms', return_value=[mf]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[rule]):
            m.gather_global(w, '123456789012', regions=['us-east-1', 'us-west-2'])
        mf_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'cloudwatch_metric_filter']
        eb_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'eventbridge_rule']
        assert {c.kwargs['region'] for c in mf_calls} == {'us-east-1', 'us-west-2'}
        assert {c.kwargs['region'] for c in eb_calls} == {'us-east-1', 'us-west-2'}

    def test_a_per_region_trails_failure_does_not_abort_the_other_regions(self):
        w = MagicMock()
        rule = {'Name': 'r1', '_Targets': []}

        def _trails(region):
            if region == 'us-east-1':
                raise RuntimeError('boom')
            return []
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None), \
             patch.object(m, 'get_cloudtrail_trails', side_effect=_trails), \
             patch.object(m, 'get_eventbridge_rules', return_value=[rule]):
            m.gather_global(w, '123456789012', regions=['us-east-1', 'us-west-2'])
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert 'account (cloudtrail trails, root-usage coverage)' in sources
        eb_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'eventbridge_rule']
        assert {c.kwargs['region'] for c in eb_calls} == {'us-east-1', 'us-west-2'}

    def test_role_policy_and_mfa_device_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        role = {'RoleId': 'r1', 'RoleName': 'r1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        policy = {'Arn': 'arn:p1', 'PolicyName': 'p1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        device = {'SerialNumber': 'arn:mfa1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        with patch.object(m, 'get_iam_roles', return_value=[role]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[policy]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[device]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['iam_role'].kwargs['tags'] == role['Tags']
        assert calls['iam_policy'].kwargs['tags'] == policy['Tags']
        assert calls['iam_virtual_mfa_device'].kwargs['tags'] == device['Tags']

    def test_sso_instance_and_permission_set_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        instance = {'InstanceArn': 'arn:inst1', 'IdentityStoreId': 'd-1'}
        ps = {'PermissionSetArn': 'arn:ps1', 'Name': 'AdminAccess'}
        tags_by_resource_arn = {
            'arn:inst1': [{'Key': 'lensix-suppress', 'Value': 'true'}],
            'arn:ps1': [{'Key': 'lensix-suppress-checks', 'Value': 'account_adminaccess'}],
        }
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[instance]), \
             patch.object(m, 'get_sso_permission_sets', return_value=[ps]), \
             patch.object(m, 'get_sso_tags', side_effect=lambda instance_arn, resource_arn: tags_by_resource_arn.get(resource_arn, [])), \
             patch.object(m, 'get_root_credential_report_row', return_value=None):
            m.gather_global(w, '123456789012')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['sso_instance'].kwargs['tags'] == tags_by_resource_arn['arn:inst1']
        assert calls['sso_permission_set'].kwargs['tags'] == tags_by_resource_arn['arn:ps1']

    def test_regional_eventbridge_rule_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        trail = {'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:my-lg:*'}
        rule = {'Name': 'r1', 'Arn': 'arn:rule1', '_Targets': []}
        with patch.object(m, 'get_iam_roles', return_value=[]), \
             patch.object(m, 'get_iam_groups', return_value=[]), \
             patch.object(m, 'get_iam_policies', return_value=[]), \
             patch.object(m, 'get_iam_server_certificates', return_value=[]), \
             patch.object(m, 'get_iam_virtual_mfa_devices', return_value=[]), \
             patch.object(m, 'get_password_policy', return_value={'_configured': False}), \
             patch.object(m, 'get_account_summary', return_value={}), \
             patch.object(m, 'get_support_access_roles', return_value=[]), \
             patch.object(m, 'get_sso_instances', return_value=[]), \
             patch.object(m, 'get_root_credential_report_row', return_value=None), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_metric_filters_with_alarms', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[rule]), \
             patch.object(m, 'get_eventbridge_rule_tags', return_value=[{'Key': 'lensix-suppress', 'Value': 'true'}]) as get_tags:
            m.gather_global(w, '123456789012', regions=['us-east-1'])
        get_tags.assert_called_once_with('us-east-1', 'arn:rule1')
        eb_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'eventbridge_rule']
        assert eb_calls[0].kwargs['tags'] == [{'Key': 'lensix-suppress', 'Value': 'true'}]


# --- gather() orchestration --------------------------------------------------

class TestGather:
    # gather() also calls get_kms_key_tags()/get_trail_tags()/
    # get_eventbridge_rule_tags()/get_config_aggregator_tags()/
    # get_log_group_tags() for real — see TestGatherGlobal's own
    # _tag_helpers fixture above for why this is an autouse default
    # rather than editing every test below individually. guardduty_detector
    # and access_analyzer need no helper here — their tags are inline on
    # the dicts get_guardduty_detectors()/get_access_analyzers() already
    # return, which every test below already controls directly.
    @pytest.fixture(autouse=True)
    def _tag_helpers(self):
        with patch.object(m, 'get_kms_key_tags', return_value=[]), \
             patch.object(m, 'get_trail_tags', return_value=[]), \
             patch.object(m, 'get_eventbridge_rule_tags', return_value=[]), \
             patch.object(m, 'get_config_aggregator_tags', return_value=[]), \
             patch.object(m, 'get_log_group_tags', return_value={}):
            yield

    def test_adds_one_resource_per_kms_key_named_from_its_alias(self):
        w = MagicMock()
        key = {'KeyId': 'k1', '_Aliases': [{'AliasName': 'alias/my-key'}]}
        with patch.object(m, 'get_kms_keys', return_value=[key]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kms_key'].kwargs['resource_name'] == 'alias/my-key'

    def test_a_kms_key_without_aliases_is_named_from_its_id(self):
        w = MagicMock()
        key = {'KeyId': 'k1', '_Aliases': []}
        with patch.object(m, 'get_kms_keys', return_value=[key]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kms_key'].kwargs['resource_name'] == 'k1'

    def test_xray_config_only_adds_a_resource_when_present(self):
        w = MagicMock()
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value={'Type': 'KMS'}):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['xray_encryption_config'].kwargs['raw'] == {'Type': 'KMS'}

    def test_adds_one_resource_per_trail_recorder_channel_aggregator_detector_analyzer_and_log_group(self):
        w = MagicMock()
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[{'TrailARN': 'arn:t1', 'Name': 'trail1'}]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[{'name': 'default'}]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[{'name': 'default'}]), \
             patch.object(m, 'get_config_aggregators', return_value=[{'ConfigurationAggregatorName': 'agg1'}]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[{'DetectorId': 'd1'}]), \
             patch.object(m, 'get_access_analyzers', return_value=[{'arn': 'arn:a1', 'name': 'analyzer1'}]), \
             patch.object(m, 'get_log_groups', return_value=[{'logGroupName': '/my/group'}]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['cloudtrail_trail'].kwargs['resource_id'] == 'arn:t1'
        assert calls['config_recorder'].kwargs['resource_id'] == 'default'
        assert calls['config_delivery_channel'].kwargs['resource_id'] == 'default'
        assert calls['config_aggregator'].kwargs['resource_id'] == 'agg1'
        assert calls['guardduty_detector'].kwargs['resource_id'] == 'd1'
        assert calls['access_analyzer'].kwargs['resource_id'] == 'arn:a1'
        assert calls['cloudwatch_log_group'].kwargs['resource_id'] == '/my/group'

    def test_adds_one_resource_per_metric_filter_and_eventbridge_rule(self):
        w = MagicMock()
        trail = {'TrailARN': 'arn:t1', 'Name': 'trail1', 'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:my-lg:*'}
        mf = {'filterName': 'my-filter', 'filterPattern': '{ $.errorCode = "AccessDenied" }', '_Alarms': []}
        rule = {'Name': 'my-rule', 'State': 'ENABLED', '_Targets': []}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_metric_filters_with_alarms', return_value=[mf]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[rule]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['cloudwatch_metric_filter'].kwargs['resource_id'] == 'my-lg:my-filter'
        assert calls['cloudwatch_metric_filter'].kwargs['raw'] == mf
        assert calls['eventbridge_rule'].kwargs['resource_id'] == 'my-rule'
        assert calls['eventbridge_rule'].kwargs['raw'] == rule

    def test_a_trail_without_a_log_group_fetches_no_metric_filters(self):
        w = MagicMock()
        trail = {'TrailARN': 'arn:t1', 'Name': 'trail1'}  # no CloudWatchLogsLogGroupArn
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_metric_filters_with_alarms') as get_metric_filters, \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        get_metric_filters.assert_not_called()

    def test_metric_filter_and_eventbridge_failures_are_isolated_from_each_other(self):
        w = MagicMock()
        trail = {'TrailARN': 'arn:t1', 'CloudWatchLogsLogGroupArn': 'arn:aws:logs:us-east-1:1:log-group:my-lg:*'}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_metric_filters_with_alarms', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_eventbridge_rules', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert sources == {'account (metric filters)', 'account (eventbridge rules)'}
        # cloudtrail_trail itself still gathered fine — only the two new
        # dependent fetches failed.
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'cloudtrail_trail' in calls

    def test_every_fetch_is_isolated_from_the_others(self):
        w = MagicMock()
        with patch.object(m, 'get_kms_keys', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_cloudtrail_trails', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_eventbridge_rules', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_config_recorders', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_config_delivery_channels', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_config_aggregators', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_guardduty_detectors', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_access_analyzers', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_log_groups', side_effect=RuntimeError('boom')), \
             patch.object(m, 'get_xray_encryption_config', side_effect=RuntimeError('boom')):
            m.gather('us-east-1', w)
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert sources == {
            'account (kms keys)', 'account (cloudtrail trails)', 'account (config recorders)',
            'account (config delivery channels)', 'account (config aggregators)',
            'account (guardduty detectors)', 'account (access analyzers)',
            'account (log groups)', 'account (xray encryption config)', 'account (eventbridge rules)',
        }
        w.add_resource.assert_not_called()

    def test_kms_key_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        key = {'KeyId': 'k1', '_Aliases': []}
        with patch.object(m, 'get_kms_keys', return_value=[key]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None), \
             patch.object(m, 'get_kms_key_tags', return_value=[{'Key': 'lensix-suppress', 'Value': 'true'}]) as get_tags:
            m.gather('us-east-1', w)
        get_tags.assert_called_once_with('us-east-1', 'k1')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kms_key'].kwargs['tags'] == [{'Key': 'lensix-suppress', 'Value': 'true'}]

    def test_trail_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        trail = {'TrailARN': 'arn:t1', 'Name': 'trail1'}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[trail]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None), \
             patch.object(m, 'get_trail_tags', return_value=[{'Key': 'lensix-suppress', 'Value': 'true'}]) as get_tags:
            m.gather('us-east-1', w)
        get_tags.assert_called_once_with('us-east-1', 'arn:t1')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['cloudtrail_trail'].kwargs['tags'] == [{'Key': 'lensix-suppress', 'Value': 'true'}]

    def test_regional_eventbridge_rule_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        rule = {'Name': 'my-rule', 'Arn': 'arn:rule1', '_Targets': []}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[rule]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None), \
             patch.object(m, 'get_eventbridge_rule_tags', return_value=[{'Key': 'lensix-suppress', 'Value': 'true'}]) as get_tags:
            m.gather('us-east-1', w)
        get_tags.assert_called_once_with('us-east-1', 'arn:rule1')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['eventbridge_rule'].kwargs['tags'] == [{'Key': 'lensix-suppress', 'Value': 'true'}]

    def test_config_aggregator_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        agg = {'ConfigurationAggregatorName': 'agg1', 'ConfigurationAggregatorArn': 'arn:agg1'}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[agg]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None), \
             patch.object(m, 'get_config_aggregator_tags', return_value=[{'Key': 'lensix-suppress', 'Value': 'true'}]) as get_tags:
            m.gather('us-east-1', w)
        get_tags.assert_called_once_with('us-east-1', 'arn:agg1')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['config_aggregator'].kwargs['tags'] == [{'Key': 'lensix-suppress', 'Value': 'true'}]

    def test_guardduty_detector_and_access_analyzer_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        detector = {'DetectorId': 'd1', 'Tags': {'lensix-suppress': 'true'}}
        analyzer = {'arn': 'arn:a1', 'name': 'analyzer1', 'tags': {'lensix-suppress': 'true'}}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[detector]), \
             patch.object(m, 'get_access_analyzers', return_value=[analyzer]), \
             patch.object(m, 'get_log_groups', return_value=[]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['guardduty_detector'].kwargs['tags'] == {'lensix-suppress': 'true'}
        assert calls['access_analyzer'].kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_log_group_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        lg = {'logGroupName': '/my/group', 'arn': 'arn:lg1'}
        with patch.object(m, 'get_kms_keys', return_value=[]), \
             patch.object(m, 'get_cloudtrail_trails', return_value=[]), \
             patch.object(m, 'get_eventbridge_rules', return_value=[]), \
             patch.object(m, 'get_config_recorders', return_value=[]), \
             patch.object(m, 'get_config_delivery_channels', return_value=[]), \
             patch.object(m, 'get_config_aggregators', return_value=[]), \
             patch.object(m, 'get_guardduty_detectors', return_value=[]), \
             patch.object(m, 'get_access_analyzers', return_value=[]), \
             patch.object(m, 'get_log_groups', return_value=[lg]), \
             patch.object(m, 'get_xray_encryption_config', return_value=None), \
             patch.object(m, 'get_log_group_tags', return_value={'lensix-suppress': 'true'}) as get_tags:
            m.gather('us-east-1', w)
        get_tags.assert_called_once_with('us-east-1', 'arn:lg1')
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['cloudwatch_log_group'].kwargs['tags'] == {'lensix-suppress': 'true'}
