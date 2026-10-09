"""Regression coverage for the October safety/compatibility review."""

import copy
import curses
import io
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from nputop import cli, preview
from nputop.api import libascend, monitor
from nputop.gui.monitor import Dashboard
from test_monitor import fake_frame, interaction_frame, options
from test_libascend_a5 import A5_OUTPUT


def test_help_never_confirms_a_hidden_signal(monkeypatch):
    frame = interaction_frame()
    for p in frame['processes']:
        p.update(signal_allowed=True, pid_namespace='test')
    screen = Dashboard(options())
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, Mock())
    screen.help = True
    sender = Mock()
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', sender)
    for key in ('k', 'y', 'r', 's'):
        screen.handle(ord(key), frame, Mock())
    assert screen.confirm is None and screen.help
    sender.assert_not_called()
    assert screen.handle(ord('q'), frame, Mock()) is True
    assert not screen.help


def test_unverified_namespace_does_not_resolve_colliding_pid(monkeypatch):
    monkeypatch.setattr(monitor, 'host_pid_namespace', lambda: None)
    manager = monitor.BackendManager('dcmi')
    lookup = Mock(side_effect=AssertionError('must not look up driver PID locally'))
    monkeypatch.setattr(monitor.psutil, 'Process', lookup)
    rows = [dict(pid=1002, container_pid=101, device='0:0', memory=128)]
    manager._enrich_processes(rows)
    assert rows[0]['container_pid'] == 101
    assert rows[0]['created'] is None and rows[0]['signal_allowed'] is False
    lookup.assert_not_called()
    frame = fake_frame()
    frame['processes'] = [dict(rows[0], device='5:0')]
    screen = Dashboard(options())
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, Mock())
    screen.handle(ord('k'), frame, SimpleNamespace(error=None))
    assert screen.confirm is None and 'unverified' in screen.notice


def test_signal_rechecks_namespace_even_with_matching_creation_time(monkeypatch):
    frame = interaction_frame()
    row = frame['processes'][0]
    row.update(signal_allowed=True, pid_namespace='previous')
    screen = Dashboard(options())
    screen.confirm = dict(row)
    monkeypatch.setattr('nputop.gui.actions.host_pid_namespace', lambda: None)
    lookup = Mock()
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', lookup)
    screen.handle(ord('y'), frame, SimpleNamespace(error=None))
    lookup.assert_not_called()
    assert 'namespace' in screen.notice


@pytest.mark.parametrize('action', ['terminate', 'kill', 'interrupt'])
def test_legacy_signals_require_verified_namespace(monkeypatch, action):
    from nputop.gui.library.selection import Selection

    monkeypatch.setattr(monitor, 'host_pid_namespace', lambda: None)
    selection = Selection(None)
    process = Mock()
    monkeypatch.setattr(selection, 'processes', lambda: (process,))
    getattr(selection, action)()
    assert not process.mock_calls
    monkeypatch.setattr(monitor, 'host_pid_namespace', lambda: 'verified')
    getattr(selection, action)()
    assert len(process.mock_calls) == 1


def test_legacy_namespace_notice_uses_a_valid_read_only_dialog(monkeypatch):
    from nputop.gui.library import messagebox

    monkeypatch.setattr(monitor, 'host_pid_namespace', lambda: None)
    root = SimpleNamespace(x=0, y=0, keymaps=SimpleNamespace(used_keymap=None))
    # Exercise the real constructor and its option assertions without a terminal.
    monkeypatch.setattr(messagebox.MessageBox, 'init_keybindings', lambda self: None)
    panel = SimpleNamespace(root=root, win=None)
    messagebox.send_signal('kill', panel)
    assert 'Read-only' in root.messagebox.message
    assert all(option.callback is None for option in root.messagebox.options)


def test_verified_host_can_enrich_and_terminate_its_owned_test_child():
    if monitor.host_pid_namespace() is None:
        pytest.skip('requires the initial Linux PID namespace')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        frame = fake_frame()
        frame['processes'] = [dict(device='5:0', pid=child.pid, memory=0)]
        monitor.BackendManager('dcmi')._enrich_processes(frame['processes'])
        assert frame['processes'][0]['signal_allowed'] is True
        screen = Dashboard(options())
        screen.focus = 'processes'
        sampler = SimpleNamespace(error=None)
        screen.handle(curses.KEY_HOME, frame, sampler)
        screen.handle(ord('T'), frame, sampler)
        assert screen.confirm['pid'] == child.pid
        screen.handle(ord('y'), frame, sampler)
        assert child.wait(timeout=3) == -15
        assert 'SIGTERM sent' in screen.notice
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def test_host_namespace_proof_fails_closed(monkeypatch):
    monkeypatch.setattr(psutil, 'PROCFS_PATH', '/proc', raising=False)
    monkeypatch.setattr(monitor.os, 'readlink', lambda p: 'pid:[4026531836]')
    monkeypatch.setattr(Path, 'read_text', lambda self: 'NSpid:\t%d\n' % os.getpid())
    assert monitor.host_pid_namespace() == 'pid:[4026531836]'
    monkeypatch.setattr(monitor.os, 'readlink', lambda p: 'pid:[4026533000]')
    assert monitor.host_pid_namespace() is None
    monkeypatch.setattr(monitor.os, 'readlink', lambda p: 'pid:[4026531836]')
    monkeypatch.setattr(Path, 'read_text', lambda self: 'NSpid:\t999 1\n')
    assert monitor.host_pid_namespace() is None
    monkeypatch.setattr(Path, 'read_text', lambda self: 'Name:\tnputop\n')
    assert monitor.host_pid_namespace() is None


def test_smi_preserves_container_pid_without_rewriting_driver_pid(monkeypatch):
    monkeypatch.setattr(
        monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=A5_OUTPUT)
    )
    monkeypatch.setattr(libascend, '_smi_timeout', lambda: 3)
    frame = monitor.SmiAdapter().sample()
    row = next(p for p in frame['processes'] if p['pid'] == 1002)
    assert row['container_pid'] == 101
    assert libascend._CACHE[0]['procs'][1][0] == 1002  # public legacy tuple unchanged
    raw = A5_OUTPUT.replace('| 101                     |', '| NA                      |')
    monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
    assert (
        next(p for p in monitor.SmiAdapter().sample()['processes'] if p['pid'] == 1002)[
            'container_pid'
        ]
        is None
    )


def large_frame():
    frame = fake_frame()
    frame['devices'] = [copy.deepcopy(frame['devices'][0]) for _ in range(16)]
    for i, row in enumerate(frame['devices']):
        row.update(id=i, key='%d:%d' % (i // 2, i % 2), card=i // 2, chip=i % 2)
    frame['processes'] = [
        dict(device=r['key'], pid=1000 + i, memory=1024, command='COMMAND_%d' % i)
        for i, r in enumerate(frame['devices'])
    ]
    return frame


class ImmediateSampler:
    error = None
    inflight = None
    frame = None

    def __init__(self, *args):
        self.frame = large_frame()

    def poll(self, *args):
        return self.frame

    def close(self):
        pass


def test_once_renders_all_groups_and_applies_filters(monkeypatch, capsys):
    monkeypatch.setattr(preview, 'Sampler', ImmediateSampler)
    assert preview.main(['--once']) == 0
    out = capsys.readouterr().out
    assert sum('COMMAND_' in l for l in out.splitlines()) == 16
    assert sum('120W' in l for l in out.splitlines()) == 8  # shared by card, rounded for display
    assert preview.main(['--once', '--only', '2', '--pid', '1002']) == 0
    out = capsys.readouterr().out
    assert sum('COMMAND_' in l for l in out.splitlines()) == 1
    assert 'COMMAND_2' in out


@pytest.mark.parametrize('flag', ['--no-unicode', '--colorful', '--light', '--force-color'])
def test_old_cli_options_remain_on_original_parser(monkeypatch, flag):
    monkeypatch.setattr(sys, 'argv', ['nputop', flag, '--once'])
    parsed = cli.parse_arguments()
    monkeypatch.setattr(sys, 'argv', ['nputop', '--legacy-ui', flag, '--once'])
    called = Mock(side_effect=RuntimeError('legacy parser reached'))
    monkeypatch.setattr(cli, 'parse_arguments', called)
    with pytest.raises(RuntimeError, match='legacy parser reached'):
        cli.main()
    assert parsed.once


@pytest.mark.parametrize('alias', [[], ['--preview']])
def test_native_dashboard_is_default_with_compatible_preview_alias(monkeypatch, alias):
    argv = ['nputop'] + alias + ['--once']
    monkeypatch.setattr(sys, 'argv', argv)
    entry = Mock(return_value=0)
    monkeypatch.setattr(preview, 'main', entry)
    assert cli.main() == 0
    entry.assert_called_once_with(['--once'])
    assert sys.argv == argv


def test_stale_aggregates_do_not_create_valid_history():
    frame = fake_frame()
    m = frame['devices'][0]['metrics']
    m['memory_used']['state'] = 'stale'
    m['npu_overall'] = dict(value=80, state='stale')
    screen = Dashboard(options())
    lines = screen.lines(frame, 160, 60)
    assert list(screen.history['@mem']) == [None]
    assert list(screen.history['@util']) == [None]
    text = '\n'.join(x.text for x in lines)
    assert 'NPU MEM -- - 0/1 chips' in text
    assert 'AVG NPU UTL -- - 0/1 chips' in text
    frame['sampled_at'] += 1
    m['memory_used']['state'] = m['npu_overall']['state'] = 'ok'
    screen.lines(frame, 160, 60)
    assert screen.history['@mem'][-1] == 25
    assert screen.history['@util'][-1] == 80


@pytest.mark.parametrize('state', ['main', 'help', 'confirm', 'loading', 'error'])
def test_every_ascii_screen_is_encodable(state):
    screen = Dashboard(options(ascii=True))
    frame = interaction_frame()
    frame['processes'][0]['command'] = '测试程序 → --参数'
    if state == 'help':
        screen.help = True
    if state == 'confirm':
        screen.confirm = frame['processes'][0]
    if state == 'loading':
        frame = None
    error = '驱动失效' if state == 'error' else None
    text = '\n'.join(x.text for x in screen.lines(frame, 160, 60, error))
    text.encode('ascii')


def test_low_encoding_stdout_automatically_uses_ascii(monkeypatch):
    monkeypatch.setattr(preview, 'Sampler', ImmediateSampler)
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding='ascii', write_through=True)
    monkeypatch.setattr(sys, 'stdout', stream)
    assert preview.main(['--once']) == 0
    assert b'COMMAND_15' in output.getvalue()


# Real lifecycle tests use only a child/group created by this test.
def resistant_worker(connection, mode):
    code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'
    child = subprocess.Popen(
        [sys.executable, '-c', code], stdout=subprocess.PIPE, universal_newlines=True
    )
    child.stdout.readline()
    connection.send(child.pid)
    if mode == 'crash':
        os._exit(3)
    time.sleep(30)


@pytest.mark.skipif(not hasattr(os, 'setsid'), reason='POSIX session cleanup')
@pytest.mark.parametrize('mode', ['running', 'crash'])
def test_sampler_cleans_resistant_descendant_before_reaping(mode):
    sampler = monitor.Sampler()
    parent, child = sampler.context.Pipe()
    sampler.session_ready = sampler.context.Event()
    process = sampler.context.Process(
        target=monitor._session_worker, args=(child, mode, sampler.session_ready, resistant_worker)
    )
    process.start()
    child.close()
    sampler.process, sampler.connection = process, parent
    assert parent.poll(5)
    descendant = parent.recv()
    owned = psutil.Process(descendant)
    try:
        if mode == 'crash':
            assert monitor.wait_connections([process.sentinel], 5)
        sampler.close()

        def alive():
            try:
                # Linux may advance from zombie to dead between observations.
                return owned.is_running() and owned.status() not in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_DEAD,
                )
            except psutil.NoSuchProcess:
                return False  # The kernel may reap between is_running() and status().

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and alive():
            time.sleep(0.01)
        assert not alive()
    finally:
        sampler.close()
        try:
            if owned.is_running() and owned.status() not in (
                psutil.STATUS_ZOMBIE,
                psutil.STATUS_DEAD,
            ):
                owned.kill()
                owned.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
