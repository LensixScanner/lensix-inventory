"""Unit tests for lensix_inventory.azure's run() orchestrator — calls
every registered gather module once against the subscription, isolating
failures per module rather than aborting the whole run. No per-region
looping (unlike AWS) — see the module's own docstring for why.

Patches the MODULES registry itself with fake gather functions, rather
than exercising every real module here — those are covered individually
in their own test files."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure as m


class TestRun:
    def test_verifies_the_credential_before_gathering_anything(self):
        credential = MagicMock()
        verify = MagicMock()
        with patch.object(m, 'get_credential', return_value=credential), \
             patch.object(m, 'get_subscription_id', return_value='sub-123'), \
             patch.object(m, 'verify_credential', verify), \
             patch.object(m, 'MODULES', []):
            writer = m.run()
        verify.assert_called_once_with(credential, 'sub-123')
        assert writer.provider == 'azure'
        assert writer.account_id == 'sub-123'

    def test_regions_argument_is_accepted_but_ignored(self):
        with patch.object(m, 'get_credential', return_value=MagicMock()), \
             patch.object(m, 'get_subscription_id', return_value='sub-123'), \
             patch.object(m, 'verify_credential'), \
             patch.object(m, 'MODULES', []):
            writer = m.run(regions=['us-east-1'])
        assert writer.account_id == 'sub-123'

    def test_each_module_is_called_once_with_credential_subscription_and_writer(self):
        credential = MagicMock()
        gather_fn = MagicMock()
        with patch.object(m, 'get_credential', return_value=credential), \
             patch.object(m, 'get_subscription_id', return_value='sub-123'), \
             patch.object(m, 'verify_credential'), \
             patch.object(m, 'MODULES', [('fake', gather_fn)]):
            writer = m.run()
        gather_fn.assert_called_once_with(credential, 'sub-123', writer)

    def test_a_failing_module_is_recorded_and_does_not_abort_the_others(self):
        good_fn = MagicMock()
        bad_fn = MagicMock(side_effect=RuntimeError('boom'))
        with patch.object(m, 'get_credential', return_value=MagicMock()), \
             patch.object(m, 'get_subscription_id', return_value='sub-123'), \
             patch.object(m, 'verify_credential'), \
             patch.object(m, 'MODULES', [('bad', bad_fn), ('good', good_fn)]):
            writer = m.run()
        good_fn.assert_called_once()
        assert writer.errors == [{'region': 'global', 'source': 'bad', 'message': 'boom'}]

    def test_a_failed_credential_verification_propagates_and_no_module_runs(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_credential', return_value=MagicMock()), \
             patch.object(m, 'get_subscription_id', return_value='sub-123'), \
             patch.object(m, 'verify_credential', side_effect=RuntimeError('bad credential')), \
             patch.object(m, 'MODULES', [('fake', gather_fn)]):
            try:
                m.run()
                assert False, 'expected the credential failure to propagate'
            except RuntimeError:
                pass
        gather_fn.assert_not_called()
