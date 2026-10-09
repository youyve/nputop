"""Release dispatch must remain usable without loading a hardware backend."""

import importlib
import sys
from unittest.mock import Mock

import pytest

from nputop import cli, preview, version


@pytest.mark.parametrize('flags', [[], ['--preview'], ['--preview', '--preview']])
def test_default_entry_passes_native_options_without_mutating_argv(monkeypatch, flags):
    argv = ['nputop'] + flags + ['--backend', 'smi', '--json', '--interval', '2']
    monkeypatch.setattr(sys, 'argv', argv)
    native = Mock(return_value=7)
    legacy = Mock(side_effect=AssertionError('legacy entry reached'))
    monkeypatch.setattr(preview, 'main', native)
    monkeypatch.setattr(cli, 'legacy_main', legacy)
    assert cli.main() == 7
    native.assert_called_once_with(['--backend', 'smi', '--json', '--interval', '2'])
    legacy.assert_not_called()
    assert sys.argv is argv


@pytest.mark.parametrize('flags', [['--legacy-ui'], ['--preview', '--legacy-ui']])
def test_explicit_legacy_dispatch_restores_argv_even_on_failure(monkeypatch, flags):
    argv = ['nputop'] + flags + ['--once', '--no-unicode']
    monkeypatch.setattr(sys, 'argv', argv)

    def legacy():
        assert sys.argv == ['nputop', '--once', '--no-unicode']
        raise RuntimeError('owned test failure')

    monkeypatch.setattr(cli, 'legacy_main', legacy)
    with pytest.raises(RuntimeError, match='owned test failure'):
        cli.main()
    assert sys.argv is argv


def test_release_version_does_not_depend_on_git_or_launch_subprocesses(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, 'check_output', Mock(side_effect=AssertionError('git invoked')))
    importlib.reload(version)
    assert version.__version__ == '0.1.0'
    assert version.__release__ is True
    assert version.__preview_version__ == version.__version__


@pytest.mark.parametrize('flags', [[], ['--preview'], ['--legacy-ui']])
def test_all_entries_report_same_version_without_a_driver(monkeypatch, capsys, flags):
    monkeypatch.setattr(sys, 'argv', ['nputop'] + flags + ['--version'])
    monkeypatch.setattr(cli.Device, 'count', Mock(side_effect=AssertionError('driver queried')))
    monkeypatch.setattr(preview, 'Sampler', Mock(side_effect=AssertionError('sampler started')))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert capsys.readouterr().out.strip() == 'nputop 0.1.0'


def test_default_help_explains_backends_and_ui_compatibility(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['nputop', '--help'])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert '--backend' in text and '--legacy-ui' in text and '--preview' in text
    assert 'development preview' not in text


def test_legacy_without_accessible_devices_is_a_clear_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['nputop', '--legacy-ui', '--once'])
    monkeypatch.setattr(cli.Device, 'count', lambda: 0)
    assert cli.main() == 1
    assert 'No accessible Ascend NPU devices' in capsys.readouterr().err


@pytest.mark.parametrize('flags', [['--json'], ['--once']])
def test_invalid_display_id_fails_without_a_partial_report(monkeypatch, capsys, flags):
    from test_monitor import fake_frame

    sampler = Mock()
    sampler.poll.return_value = fake_frame()
    monkeypatch.setattr(preview, 'Sampler', lambda *args: sampler)
    assert preview.main(flags + ['--only', '0', '99']) == 2
    output = capsys.readouterr()
    assert not output.out and 'Invalid display device indices: 99' in output.err
    sampler.close.assert_called_once()


def test_invalid_selection_does_not_enter_curses(monkeypatch, capsys):
    from nputop.gui import monitor as gui
    from test_monitor import fake_frame

    sampler = Mock(frame=fake_frame())
    monkeypatch.setattr(gui, 'wait_for_frame', lambda *args: True)
    monkeypatch.setattr(gui.curses, 'wrapper', Mock(side_effect=AssertionError('TUI entered')))
    args = preview.parse_arguments(['--only', '99'])
    assert gui.run_dashboard(sampler, args) == 2
    assert 'Invalid display device indices' in capsys.readouterr().err


def test_explicit_empty_visibility_does_not_require_a_mapping(monkeypatch, capsys):
    import json
    from test_monitor import fake_frame

    sampler = Mock()
    sampler.poll.return_value = fake_frame()
    monkeypatch.setattr(preview, 'Sampler', lambda *args: sampler)
    monkeypatch.setenv('ASCEND_RT_VISIBLE_DEVICES', '')
    assert preview.main(['--json', '--only-visible']) == 0
    assert json.loads(capsys.readouterr().out)['devices'] == []
