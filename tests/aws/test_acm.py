"""Unit tests for lensix_inventory.aws.acm — ACM certificates."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.acm as m


def _acm_client(arns, detail_by_arn=None, detail_error_arns=None, tags_by_arn=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {'CertificateSummaryList': [{'CertificateArn': a} for a in arns]}
    ]
    detail_by_arn = detail_by_arn or {}
    detail_error_arns = detail_error_arns or set()

    def _describe(CertificateArn):
        if CertificateArn in detail_error_arns:
            raise RuntimeError('boom')
        return {'Certificate': detail_by_arn[CertificateArn]}
    client.describe_certificate.side_effect = _describe
    tags_by_arn = tags_by_arn or {}
    client.list_tags_for_certificate.side_effect = lambda CertificateArn: {'Tags': tags_by_arn.get(CertificateArn, [])}
    return client


class TestGather:
    def test_adds_one_resource_per_certificate(self):
        w = MagicMock()
        cert = {'DomainName': 'example.com', 'Status': 'ISSUED'}
        client = _acm_client(['arn:cert1'], detail_by_arn={'arn:cert1': cert})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='acm_certificate', region='us-east-1',
            resource_id='arn:cert1', resource_name='example.com', raw=cert,
            tags=[],
        )

    def test_certificate_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cert = {'DomainName': 'example.com'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _acm_client(['arn:cert1'], detail_by_arn={'arn:cert1': cert}, tags_by_arn={'arn:cert1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_falls_back_to_the_arn_when_domain_name_missing(self):
        w = MagicMock()
        cert = {'Status': 'ISSUED'}
        client = _acm_client(['arn:cert1'], detail_by_arn={'arn:cert1': cert})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'arn:cert1'

    def test_a_describe_failure_for_one_certificate_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'DomainName': 'good.com'}
        client = _acm_client(['bad', 'good'], detail_by_arn={'good': good}, detail_error_arns={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'acm_certificate:bad'
        w.add_resource.assert_called_once()

    def test_no_certificates_gathers_nothing(self):
        w = MagicMock()
        client = _acm_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
