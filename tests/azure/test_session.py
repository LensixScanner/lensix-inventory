"""Unit tests for lensix_inventory.azure.session — credential/subscription
discovery via the local Azure credential chain."""

from unittest.mock import MagicMock, patch

import pytest

import lensix_inventory.azure.session as m


class TestGetCredential:
    def test_returns_a_default_azure_credential(self):
        fake = MagicMock()
        with patch.object(m, 'DefaultAzureCredential', return_value=fake) as ctor:
            assert m.get_credential() is fake
        ctor.assert_called_once_with()


class TestGetSubscriptionId:
    def test_reads_from_the_env_var(self, monkeypatch):
        monkeypatch.setenv('AZURE_SUBSCRIPTION_ID', 'sub-123')
        assert m.get_subscription_id() == 'sub-123'

    def test_raises_when_unset(self, monkeypatch):
        monkeypatch.delenv('AZURE_SUBSCRIPTION_ID', raising=False)
        with pytest.raises(ValueError):
            m.get_subscription_id()

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv('AZURE_SUBSCRIPTION_ID', '')
        with pytest.raises(ValueError):
            m.get_subscription_id()


class TestVerifyCredential:
    def test_calls_subscriptions_get_with_the_given_id(self):
        client = MagicMock()
        credential = MagicMock()
        with patch.object(m, 'SubscriptionClient', return_value=client) as ctor:
            m.verify_credential(credential, 'sub-123')
        ctor.assert_called_once_with(credential)
        client.subscriptions.get.assert_called_once_with('sub-123')

    def test_propagates_a_failure_from_the_underlying_call(self):
        client = MagicMock()
        client.subscriptions.get.side_effect = RuntimeError('unauthorized')
        with patch.object(m, 'SubscriptionClient', return_value=client):
            with pytest.raises(RuntimeError):
                m.verify_credential(MagicMock(), 'sub-123')
