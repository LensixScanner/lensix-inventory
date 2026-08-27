"""Unit tests for lensix_inventory.gcp's run() orchestrator — calls every
registered gather module once against the project, isolating failures per
module rather than aborting the whole run. No per-region looping — see
the module's own docstring for why.

Patches the MODULES registry itself with fake gather functions, rather
than exercising every real module here — those are covered individually
in their own test files."""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp as m


class TestRun:
    def test_verifies_the_credentials_before_gathering_anything(self):
        verify = MagicMock()
        with patch.object(m, 'get_project_id', return_value='proj-1'), \
             patch.object(m, 'get_credentials', return_value='creds'), \
             patch.object(m, 'verify_credentials', verify), \
             patch.object(m, 'MODULES', []):
            writer = m.run()
        verify.assert_called_once_with('creds', 'proj-1')
        assert writer.provider == 'gcp'
        assert writer.account_id == 'proj-1'

    def test_regions_argument_is_accepted_but_ignored(self):
        with patch.object(m, 'get_project_id', return_value='proj-1'), \
             patch.object(m, 'get_credentials', return_value='creds'), \
             patch.object(m, 'verify_credentials'), \
             patch.object(m, 'MODULES', []):
            writer = m.run(regions=['us-east-1'])
        assert writer.account_id == 'proj-1'

    def test_each_module_is_called_once_with_project_id_credentials_and_writer(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_project_id', return_value='proj-1'), \
             patch.object(m, 'get_credentials', return_value='creds'), \
             patch.object(m, 'verify_credentials'), \
             patch.object(m, 'MODULES', [('fake', gather_fn)]):
            writer = m.run()
        gather_fn.assert_called_once_with('proj-1', 'creds', writer)

    def test_a_failing_module_is_recorded_and_does_not_abort_the_others(self):
        good_fn = MagicMock()
        bad_fn = MagicMock(side_effect=RuntimeError('boom'))
        with patch.object(m, 'get_project_id', return_value='proj-1'), \
             patch.object(m, 'get_credentials', return_value='creds'), \
             patch.object(m, 'verify_credentials'), \
             patch.object(m, 'MODULES', [('bad', bad_fn), ('good', good_fn)]):
            writer = m.run()
        good_fn.assert_called_once()
        assert writer.errors == [{'region': 'global', 'source': 'bad', 'message': 'boom'}]

    def test_a_failed_credential_verification_propagates_and_no_module_runs(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_project_id', return_value='proj-1'), \
             patch.object(m, 'get_credentials', return_value='creds'), \
             patch.object(m, 'verify_credentials', side_effect=RuntimeError('bad credential')), \
             patch.object(m, 'MODULES', [('fake', gather_fn)]):
            try:
                m.run()
                assert False, 'expected the credential failure to propagate'
            except RuntimeError:
                pass
        gather_fn.assert_not_called()
