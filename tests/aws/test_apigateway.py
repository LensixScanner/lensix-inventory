"""Unit tests for lensix_inventory.aws.apigateway — REST/HTTP APIs, stages, and custom domains."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import lensix_inventory.aws.apigateway as m


def _clients(rest_apis=None, rest_apis_raise=False, stages_by_api=None, stages_error_apis=None,
             http_apis=None, http_apis_raise=False, http_stages_by_api=None, http_stages_error_apis=None,
             v1_domains=None, v1_domains_raise=False, v2_domains=None, v2_domains_raise=False,
             web_acl_by_stage_arn=None, web_acl_not_found_arns=None, web_acl_error_arns=None):
    apigw = MagicMock()

    def _apigw_paginator(op_name):
        p = MagicMock()
        if op_name == 'get_rest_apis':
            if rest_apis_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'items': rest_apis or []}]
        elif op_name == 'get_domain_names':
            if v1_domains_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'items': v1_domains or []}]
        return p
    apigw.get_paginator.side_effect = _apigw_paginator

    stages_by_api = stages_by_api or {}
    stages_error_apis = stages_error_apis or set()

    def _get_stages(restApiId):
        if restApiId in stages_error_apis:
            raise RuntimeError('boom')
        return {'item': stages_by_api.get(restApiId, [])}
    apigw.get_stages.side_effect = _get_stages

    apigwv2 = MagicMock()

    def _apigwv2_paginator(op_name):
        p = MagicMock()
        if op_name == 'get_apis':
            if http_apis_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'Items': http_apis or []}]
        elif op_name == 'get_stages':
            def _paginate(ApiId):
                if ApiId in (http_stages_error_apis or set()):
                    raise RuntimeError('boom')
                return [{'Items': (http_stages_by_api or {}).get(ApiId, [])}]
            p.paginate.side_effect = _paginate
        elif op_name == 'get_domain_names':
            if v2_domains_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'Items': v2_domains or []}]
        return p
    apigwv2.get_paginator.side_effect = _apigwv2_paginator

    wafv2 = MagicMock()
    web_acl_by_stage_arn = web_acl_by_stage_arn or {}
    web_acl_not_found_arns = web_acl_not_found_arns or set()
    web_acl_error_arns = web_acl_error_arns or set()

    def _get_web_acl(ResourceArn):
        if ResourceArn in web_acl_not_found_arns:
            raise ClientError({'Error': {'Code': 'WAFNonexistentItemException'}}, 'GetWebACLForResource')
        if ResourceArn in web_acl_error_arns:
            raise ClientError({'Error': {'Code': 'InternalFailure'}}, 'GetWebACLForResource')
        return {'WebACL': web_acl_by_stage_arn.get(ResourceArn)}
    wafv2.get_web_acl_for_resource.side_effect = _get_web_acl

    def _client(service, region_name=None):
        return {'apigateway': apigw, 'apigatewayv2': apigwv2, 'wafv2': wafv2}[service]
    return _client


class TestGetStageWebAcl:
    def test_returns_none_for_wafnonexistentitem(self):
        client_fn = _clients(web_acl_not_found_arns={'arn:aws:apigateway:us-east-1::/restapis/a1/stages/prod'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            assert m.get_stage_web_acl('us-east-1', 'a1', 'prod') is None

    def test_reraises_an_unrelated_client_error(self):
        client_fn = _clients(web_acl_error_arns={'arn:aws:apigateway:us-east-1::/restapis/a1/stages/prod'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            try:
                m.get_stage_web_acl('us-east-1', 'a1', 'prod')
                assert False, 'expected the ClientError to propagate'
            except ClientError:
                pass


class TestGather:
    def test_adds_one_resource_per_rest_api(self):
        w = MagicMock()
        api = {'id': 'a1', 'name': 'my-api'}
        client_fn = _clients(rest_apis=[api])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_rest_api']
        assert calls[0].kwargs['resource_id'] == 'a1'

    def test_adds_one_resource_per_rest_stage_with_web_acl_merged_in(self):
        w = MagicMock()
        api = {'id': 'a1', 'name': 'my-api'}
        stage = {'stageName': 'prod'}
        client_fn = _clients(
            rest_apis=[api], stages_by_api={'a1': [stage]},
            web_acl_by_stage_arn={'arn:aws:apigateway:us-east-1::/restapis/a1/stages/prod': {'Name': 'my-acl'}},
        )
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_stage']
        assert calls[0].kwargs['resource_id'] == 'a1/prod'
        assert calls[0].kwargs['raw']['_WebAcl'] == {'Name': 'my-acl'}
        assert calls[0].kwargs['raw']['_ApiType'] == 'REST'

    def test_a_rest_stages_failure_for_one_api_does_not_abort_the_others(self):
        w = MagicMock()
        bad_api = {'id': 'bad', 'name': 'bad'}
        good_api = {'id': 'good', 'name': 'good'}
        client_fn = _clients(rest_apis=[bad_api, good_api], stages_by_api={'good': [{'stageName': 'prod'}]}, stages_error_apis={'bad'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'apigw_stage:bad' for c in w.add_error.call_args_list)
        stage_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_stage']
        assert len(stage_calls) == 1

    def test_a_stage_web_acl_failure_still_records_the_stage(self):
        w = MagicMock()
        api = {'id': 'a1', 'name': 'my-api'}
        stage = {'stageName': 'prod'}
        client_fn = _clients(rest_apis=[api], stages_by_api={'a1': [stage]},
                              web_acl_error_arns={'arn:aws:apigateway:us-east-1::/restapis/a1/stages/prod'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any('apigw_stage:a1/prod' == c.kwargs['source'] for c in w.add_error.call_args_list)
        calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_stage']
        assert len(calls) == 1

    def test_adds_one_resource_per_http_api_and_its_stages_without_a_web_acl_lookup(self):
        w = MagicMock()
        api = {'ApiId': 'h1', 'Name': 'my-http-api'}
        stage = {'StageName': 'prod'}
        client_fn = _clients(http_apis=[api], http_stages_by_api={'h1': [stage]})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        api_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_http_api']
        stage_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_stage']
        assert api_calls[0].kwargs['resource_id'] == 'h1'
        assert stage_calls[0].kwargs['resource_id'] == 'h1/prod'
        assert stage_calls[0].kwargs['raw']['_ApiType'] == 'HTTP'
        assert '_WebAcl' not in stage_calls[0].kwargs['raw']

    def test_a_http_stages_failure_for_one_api_does_not_abort_the_others(self):
        w = MagicMock()
        bad_api = {'ApiId': 'bad'}
        good_api = {'ApiId': 'good'}
        client_fn = _clients(http_apis=[bad_api, good_api], http_stages_by_api={'good': [{'StageName': 'prod'}]}, http_stages_error_apis={'bad'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'apigw_stage:bad' for c in w.add_error.call_args_list)

    def test_adds_one_resource_per_v1_domain(self):
        w = MagicMock()
        domain = {'domainName': 'api.example.com'}
        client_fn = _clients(v1_domains=[domain])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_domain']
        assert calls[0].kwargs['raw']['_ApiType'] == 'REST'

    def test_adds_one_resource_per_v2_domain(self):
        w = MagicMock()
        domain = {'DomainName': 'api2.example.com'}
        client_fn = _clients(v2_domains=[domain])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'apigw_domain']
        assert calls[0].kwargs['raw']['_ApiType'] == 'HTTP'

    def test_each_of_the_four_top_level_fetches_is_isolated_from_the_others(self):
        w = MagicMock()
        client_fn = _clients(rest_apis_raise=True, http_apis_raise=True, v1_domains_raise=True, v2_domains_raise=True)
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        sources = {c.kwargs['source'] for c in w.add_error.call_args_list}
        assert sources == {
            'apigateway (rest apis)', 'apigateway (http apis)',
            'apigateway (v1 domains)', 'apigateway (v2 domains)',
        }
        w.add_resource.assert_not_called()
