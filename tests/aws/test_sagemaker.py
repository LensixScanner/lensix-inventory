"""Unit tests for lensix_inventory.aws.sagemaker — SageMaker notebook instances."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.sagemaker as m


def _sm_client(notebooks, detail_by_name=None, detail_error_names=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'NotebookInstances': notebooks}]
    detail_by_name = detail_by_name or {}
    detail_error_names = detail_error_names or set()

    def _describe(NotebookInstanceName):
        if NotebookInstanceName in detail_error_names:
            raise RuntimeError('boom')
        return detail_by_name[NotebookInstanceName]
    client.describe_notebook_instance.side_effect = _describe
    return client


class TestGather:
    def test_adds_one_resource_per_notebook_using_the_describe_result(self):
        w = MagicMock()
        summary = {'NotebookInstanceName': 'nb1', 'NotebookInstanceArn': 'arn:aws:sagemaker:us-east-1:1:notebook-instance/nb1'}
        detail = {'NotebookInstanceName': 'nb1', 'InstanceType': 'ml.t2.medium'}
        client = _sm_client([summary], detail_by_name={'nb1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='sagemaker_notebook', region='us-east-1',
            resource_id='arn:aws:sagemaker:us-east-1:1:notebook-instance/nb1',
            resource_name='nb1', raw=detail,
        )

    def test_a_describe_failure_for_one_notebook_does_not_abort_the_others(self):
        w = MagicMock()
        bad = {'NotebookInstanceName': 'bad', 'NotebookInstanceArn': 'arn:bad'}
        good = {'NotebookInstanceName': 'good', 'NotebookInstanceArn': 'arn:good'}
        client = _sm_client([bad, good], detail_by_name={'good': {'NotebookInstanceName': 'good'}}, detail_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'sagemaker_notebook:arn:bad'
        w.add_resource.assert_called_once()

    def test_no_notebooks_gathers_nothing(self):
        w = MagicMock()
        client = _sm_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
