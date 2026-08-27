"""Unit tests for lensix_inventory.cli — the `python -m lensix_inventory`
entry point (argument parsing, provider dispatch, and the end-of-run
summary)."""

from unittest.mock import MagicMock, patch

import lensix_inventory.cli as cli


def _writer(records_count=0, errors=None, resource_counts=None, regions=None):
    w = MagicMock()
    w.errors = errors or []
    w.write.return_value = {
        'total_resources': records_count,
        'regions': regions or [],
        'resource_counts': resource_counts or {},
    }
    return w


class TestLoadProvider:
    def test_imports_the_requested_provider_lazily_and_returns_its_run_fn(self):
        fake_module = MagicMock()
        fake_module.run = 'the-run-function'
        with patch.object(cli.importlib, 'import_module', return_value=fake_module) as m:
            result = cli._load_provider('aws')
        m.assert_called_once_with('.aws', package='lensix_inventory')
        assert result == 'the-run-function'


class TestMain:
    def test_happy_path_returns_zero_and_writes_the_output_file(self, tmp_path):
        writer = _writer(records_count=3, regions=['us-east-1'], resource_counts={'ec2_instance': 3})
        run_fn = MagicMock(return_value=writer)
        out = str(tmp_path / 'out.ndjson.gz')

        with patch.object(cli, '_load_provider', return_value=run_fn):
            code = cli.main(['--provider', 'aws', '--output', out])

        assert code == 0
        run_fn.assert_called_once_with(regions=None)
        writer.write.assert_called_once_with(out)

    def test_regions_flag_is_split_on_commas_and_passed_through(self, tmp_path):
        writer = _writer()
        run_fn = MagicMock(return_value=writer)
        out = str(tmp_path / 'out.ndjson.gz')

        with patch.object(cli, '_load_provider', return_value=run_fn):
            cli.main(['--provider', 'aws', '--output', out, '--regions', 'us-east-1,us-west-2'])

        run_fn.assert_called_once_with(regions=['us-east-1', 'us-west-2'])

    def test_lensix_regions_env_var_is_used_when_flag_omitted(self, tmp_path, monkeypatch):
        writer = _writer()
        run_fn = MagicMock(return_value=writer)
        out = str(tmp_path / 'out.ndjson.gz')
        monkeypatch.setenv('LENSIX_REGIONS', 'eu-west-1')

        with patch.object(cli, '_load_provider', return_value=run_fn):
            cli.main(['--provider', 'aws', '--output', out])

        run_fn.assert_called_once_with(regions=['eu-west-1'])

    def test_explicit_regions_flag_wins_over_the_env_var(self, tmp_path, monkeypatch):
        writer = _writer()
        run_fn = MagicMock(return_value=writer)
        out = str(tmp_path / 'out.ndjson.gz')
        monkeypatch.setenv('LENSIX_REGIONS', 'eu-west-1')

        with patch.object(cli, '_load_provider', return_value=run_fn):
            cli.main(['--provider', 'aws', '--output', out, '--regions', 'us-east-1'])

        run_fn.assert_called_once_with(regions=['us-east-1'])

    def test_missing_dependency_prints_install_hint_and_returns_one(self, tmp_path, capsys):
        out = str(tmp_path / 'out.ndjson.gz')
        with patch.object(cli, '_load_provider', side_effect=ModuleNotFoundError("No module named 'boto3'")):
            code = cli.main(['--provider', 'aws', '--output', out])
        assert code == 1
        captured = capsys.readouterr()
        assert 'requirements-aws.txt' in captured.err

    def test_not_implemented_provider_prints_the_message_and_returns_one(self, tmp_path, capsys):
        run_fn = MagicMock(side_effect=NotImplementedError('gcp gather is not finished yet'))
        out = str(tmp_path / 'out.ndjson.gz')
        with patch.object(cli, '_load_provider', return_value=run_fn):
            code = cli.main(['--provider', 'gcp', '--output', out])
        assert code == 1
        assert 'gcp gather is not finished yet' in capsys.readouterr().err

    def test_a_credential_error_is_surfaced_as_a_clean_one_liner_not_a_traceback(self, tmp_path, capsys):
        run_fn = MagicMock(side_effect=RuntimeError('no AWS profile configured'))
        out = str(tmp_path / 'out.ndjson.gz')
        with patch.object(cli, '_load_provider', return_value=run_fn):
            code = cli.main(['--provider', 'aws', '--output', out])
        assert code == 1
        captured = capsys.readouterr()
        assert 'RuntimeError: no AWS profile configured' in captured.err

    def test_provider_is_required(self):
        try:
            cli.main(['--output', 'out.ndjson.gz'])
            assert False, 'expected SystemExit'
        except SystemExit as e:
            assert e.code != 0

    def test_provider_must_be_one_of_the_three_known_values(self):
        try:
            cli.main(['--provider', 'oracle', '--output', 'out.ndjson.gz'])
            assert False, 'expected SystemExit'
        except SystemExit as e:
            assert e.code != 0

    def test_output_is_required(self):
        try:
            cli.main(['--provider', 'aws'])
            assert False, 'expected SystemExit'
        except SystemExit as e:
            assert e.code != 0


class TestPrintSummary:
    def test_no_errors(self, capsys):
        manifest = {'total_resources': 5, 'regions': ['us-east-1'], 'resource_counts': {'ec2_instance': 5}}
        cli._print_summary('aws', 'out.ndjson.gz', manifest, [])
        out = capsys.readouterr().out
        assert 'No errors' in out
        assert 'ec2_instance' in out
        assert 'Wrote: out.ndjson.gz' in out

    def test_permission_errors_are_grouped_separately_from_other_errors(self, capsys):
        manifest = {'total_resources': 0, 'regions': [], 'resource_counts': {}}
        errors = [
            {'region': 'us-east-1', 'source': 'workspaces', 'message': 'AccessDenied: not authorized'},
            {'region': 'us-east-1', 'source': 'ec2', 'message': 'ConnectionError: timed out'},
        ]
        cli._print_summary('aws', 'out.ndjson.gz', manifest, errors)
        out = capsys.readouterr().out
        assert 'Permission-related (1)' in out
        assert 'Other (1)' in out
        assert 'workspaces' in out
        assert 'ec2' in out

    def test_error_list_is_truncated_past_the_max_shown_per_group(self, capsys):
        manifest = {'total_resources': 0, 'regions': [], 'resource_counts': {}}
        errors = [
            {'region': 'us-east-1', 'source': f'service{i}', 'message': 'AccessDenied'}
            for i in range(cli._MAX_ERRORS_SHOWN_PER_GROUP + 3)
        ]
        cli._print_summary('aws', 'out.ndjson.gz', manifest, errors)
        out = capsys.readouterr().out
        assert '... and 3 more' in out

    def test_resource_counts_are_sorted_largest_first(self, capsys):
        manifest = {
            'total_resources': 6, 'regions': ['us-east-1'],
            'resource_counts': {'s3_bucket': 1, 'ec2_instance': 5},
        }
        cli._print_summary('aws', 'out.ndjson.gz', manifest, [])
        out = capsys.readouterr().out
        assert out.index('ec2_instance') < out.index('s3_bucket')
