"""Unit tests for lensix_inventory.aws's run() orchestrator — the loop
that calls every registered gather module across every region (plus the
global/account-wide ones once), isolating failures per module/region
rather than aborting the whole run.

Patches the REGIONAL_MODULES/GLOBAL_MODULES/GLOBAL_MODULES_WITH_ACCOUNT
registries themselves with fake gather functions, rather than exercising
every real module here — those are covered individually in their own
test files."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws as m


class TestRun:
    def test_calls_get_account_id_and_uses_it_for_the_writer(self):
        with patch.object(m, 'get_account_id', return_value='123456789012'), \
             patch.object(m, 'get_regions', return_value=[]), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            writer = m.run()
        assert writer.account_id == '123456789012'
        assert writer.provider == 'aws'

    def test_explicit_regions_bypasses_get_regions(self):
        get_regions = MagicMock(return_value=['us-east-1'])
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'get_regions', get_regions), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            m.run(regions=['eu-west-1'])
        get_regions.assert_not_called()

    def test_regional_module_is_called_once_per_region(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'REGIONAL_MODULES', [('fake', gather_fn)]), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            m.run(regions=['us-east-1', 'us-west-2'])
        assert gather_fn.call_count == 2
        called_regions = {call.args[0] for call in gather_fn.call_args_list}
        assert called_regions == {'us-east-1', 'us-west-2'}

    def test_a_failing_regional_module_does_not_abort_the_other_regions(self):
        def _fn(region, writer):
            if region == 'us-east-1':
                raise RuntimeError('boom')
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'REGIONAL_MODULES', [('fake', _fn)]), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            writer = m.run(regions=['us-east-1', 'us-west-2'])
        assert len(writer.errors) == 1
        assert writer.errors[0]['region'] == 'us-east-1'
        assert writer.errors[0]['source'] == 'fake'

    def test_a_failing_module_does_not_abort_the_other_modules_in_the_same_region(self):
        good_fn = MagicMock()
        bad_fn = MagicMock(side_effect=RuntimeError('boom'))
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'REGIONAL_MODULES', [('bad', bad_fn), ('good', good_fn)]), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            m.run(regions=['us-east-1'])
        good_fn.assert_called_once()

    def test_global_module_is_called_exactly_once_regardless_of_region_count(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', [('fake', gather_fn)]), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            m.run(regions=['us-east-1', 'us-west-2', 'eu-west-1'])
        gather_fn.assert_called_once()

    def test_a_failing_global_module_is_recorded_and_does_not_abort_the_run(self):
        gather_fn = MagicMock(side_effect=RuntimeError('boom'))
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'get_regions', return_value=[]), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', [('fake', gather_fn)]), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            writer = m.run()
        assert writer.errors == [{'region': 'global', 'source': 'fake', 'message': 'boom'}]

    def test_global_with_account_module_receives_the_account_id(self):
        gather_fn = MagicMock()
        with patch.object(m, 'get_account_id', return_value='123456789012'), \
             patch.object(m, 'get_regions', return_value=[]), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', [('fake', gather_fn)]):
            writer = m.run()
        gather_fn.assert_called_once_with(writer, '123456789012')

    def test_a_failing_global_with_account_module_is_isolated(self):
        gather_fn = MagicMock(side_effect=RuntimeError('boom'))
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'get_regions', return_value=[]), \
             patch.object(m, 'REGIONAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', [('fake', gather_fn)]):
            writer = m.run()
        assert writer.errors == [{'region': 'global', 'source': 'fake', 'message': 'boom'}]

    def test_run_returns_the_writer_with_everything_gathered(self):
        def _regional(region, writer):
            writer.add_resource(resource_type='x', region=region, resource_id='1', resource_name='1', raw={})
        with patch.object(m, 'get_account_id', return_value='123'), \
             patch.object(m, 'REGIONAL_MODULES', [('fake', _regional)]), \
             patch.object(m, 'GLOBAL_MODULES', []), \
             patch.object(m, 'GLOBAL_MODULES_WITH_ACCOUNT', []):
            writer = m.run(regions=['us-east-1'])
        assert len(writer.records) == 1
