import ctypes
import curses
import errno
import signal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from nputop.api import libdcmi, monitor
from nputop.gui.monitor import Dashboard
from test_monitor import fake_frame, options


@pytest.fixture
def proc_view(monkeypatch):
    namespace = 'pid:[4026549308]'
    files = {
        '/proc/self/status': 'NSpid:\t5\n',
        '/proc/101/status': 'NSpid:\t101\n',
        '/proc/101/stat': '101 (命令 ) 名称) S ' + '0 ' * 18 + '50',
    }
    links = {'/proc/self/ns/pid': namespace, '/proc/1/ns/pid': namespace}
    read_text = Path.read_text

    def read_file(path, *args, **kwargs):
        if str(path).startswith('/proc/'):
            if str(path) not in files:
                raise FileNotFoundError(str(path))
            value = files[str(path)]
            if isinstance(value, OSError):
                raise value
            return value
        return read_text(path, *args, **kwargs)

    def readlink(path):
        if path not in links:
            raise FileNotFoundError(path)
        return links[path]

    monkeypatch.setattr(Path, 'read_text', read_file)
    monkeypatch.setattr(monitor.os, 'readlink', readlink)
    monkeypatch.setattr(monitor.os, 'getpid', lambda: 5)
    monkeypatch.setattr(monitor.psutil, 'PROCFS_PATH', '/proc', raising=False)

    process = MagicMock()
    process.is_running.return_value = True
    process.create_time.return_value = 1
    process.username.return_value = 'test-user'
    process.cpu_percent.return_value = 12.5
    process.memory_percent.return_value = 2.5
    process.memory_info.return_value.rss = 16
    process.cmdline.return_value = ['python', 'job.py']
    lookup = Mock(return_value=process)
    monkeypatch.setattr(monitor.psutil, 'Process', lookup)
    return files, links, lookup


def test_dcmi_container_processes_are_enriched_and_can_be_signaled(proc_view):
    _, links, lookup = proc_view
    rows = [
        dict(device='5:0', pid=101, memory=128),
        dict(device='6:0', pid=101, memory=256),
    ]
    monitor.BackendManager()._enrich_processes(rows, 'container', 100)

    row = rows[0]
    lookup.assert_called_once_with(101)
    assert row['user'] == 'test-user'
    assert row['cpu'] == 12.5
    assert row['host_memory'] == 2.5
    assert row['host_rss'] == 16
    assert row['time'] > 0
    assert row['command'] == 'python job.py'
    assert row['driver_pid'] == row['local_pid'] == row['pid'] == 101
    assert row['pid_namespace'] == links['/proc/self/ns/pid']
    assert row['pid_source'] == 'dcmi-container'
    assert all(p['signal_allowed'] is True and p['start_tick'] == 50 for p in rows)

    frame = dict(fake_frame(), processes=rows)
    report = monitor.diagnostic_report(frame)
    assert report['process_identity'] == {'dcmi-container': 2}
    assert 'test-user' not in repr(report) and 'job.py' not in repr(report)
    assert row['pid_namespace'] not in repr(report)
    screen = Dashboard(options())
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, Mock())
    screen.handle(ord('T'), frame, Mock())
    assert screen.confirm['_targets'][0]['local_pid'] == 101


@pytest.mark.parametrize(
    'scope, status, memory, duplicate, expected',
    [
        (None, 'NSpid:\t101', 128, False, 'driver-pid-scope-unknown'),
        ('changed', 'NSpid:\t101', 128, False, 'driver-pid-scope-changed'),
        ('host', 'NSpid:\t101', 128, False, 'host-procfs-unavailable'),
        ('container', 'NSpid:\t101\t42', 128, False, 'target-namespace-mismatch'),
        ('container', 'NSpid:\t101', 0, False, 'unverified-npu-memory'),
        ('container', 'NSpid:\t101', 128, True, 'duplicate-driver-pid'),
        ('container', PermissionError(), 128, False, 'permission'),
        ('container', FileNotFoundError(), 128, False, 'process-exited-or-hidden'),
    ],
)
def test_unproven_pid_never_looks_up_colliding_process(
    proc_view, scope, status, memory, duplicate, expected
):
    files, _, lookup = proc_view
    files['/proc/101/status'] = status
    row = dict(device='5:0', pid=101, container_pid=5, memory=memory)
    rows = [row, dict(row)] if duplicate else [row]

    monitor.BackendManager()._enrich_processes(rows, scope, 100)

    assert all(p['host_reason'] == expected for p in rows)
    assert all(p['user'] == '?' and p['created'] is None for p in rows)
    assert all(p['signal_allowed'] is False for p in rows)
    lookup.assert_not_called()


@pytest.mark.parametrize('same_namespace', [True, False])
def test_host_procfs_maps_only_processes_in_current_namespace(proc_view, same_namespace):
    files, links, lookup = proc_view
    files['/proc/self/status'] = 'NSpid:\t800\t5\n'
    files['/proc/101/status'] = 'NSpid:\t101\t42\n'
    links['/proc/1/ns/pid'] = monitor._INITIAL_PID_NAMESPACE
    links['/proc/101/ns/pid'] = links['/proc/self/ns/pid'] if same_namespace else 'pid:[4026549309]'
    row = dict(device='5:0', pid=101, memory=128)

    monitor.BackendManager()._enrich_processes([row], 'host', 100)

    assert row['driver_pid'] == row['pid'] == 101
    assert row['signal_allowed'] is same_namespace
    if same_namespace:
        lookup.assert_called_once_with(101)
        assert row['local_pid'] == 42
        assert row['pid_source'] == 'host-procfs'
        assert row['host_state'] == 'ok'
    else:
        lookup.assert_not_called()
        assert row['host_reason'] == 'target-namespace-mismatch'


@pytest.mark.parametrize('host_namespace', [False, True])
def test_reused_pid_during_backend_sampling_is_rejected(monkeypatch, proc_view, host_namespace):
    if host_namespace:
        monkeypatch.setattr(monitor, 'host_pid_namespace', lambda: monitor._INITIAL_PID_NAMESPACE)
    _, _, lookup = proc_view
    row = dict(device='5:0', pid=101, memory=128)

    monitor.BackendManager()._enrich_processes([row], None if host_namespace else 'container', 49)

    assert row['host_reason'] == 'identity-changed'
    assert row['signal_allowed'] is False
    lookup.assert_not_called()


def test_reused_pid_during_enrichment_drops_fields_and_cached_cpu(proc_view):
    files, _, lookup = proc_view
    manager = monitor.BackendManager()
    row = dict(device='5:0', pid=101, memory=128)
    manager._enrich_processes([row], 'container', 100)
    assert row['host_state'] == 'ok'

    def replace_pid():
        files['/proc/101/stat'] = files['/proc/101/stat'].rsplit(' ', 1)[0] + ' 110'
        return ['命令已更换']

    lookup.return_value.cmdline.side_effect = replace_pid
    manager._enrich_processes([row], 'container', 100)

    assert row['host_reason'] == 'identity-changed'
    assert row['user'] == '?' and row['cpu'] is None and row['created'] is None
    assert not manager.process_cache


@pytest.mark.parametrize(
    'flag, status, scope',
    [(0, 0, 'host'), (1, 0, 'container'), (2, 0, None), (1, -1, None)],
)
def test_dcmi_pid_scope_requires_successful_known_container_flag(monkeypatch, flag, status, scope):
    def get_flag(name, ptr):
        assert name == 'dmanage_get_container_flag'
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint))[0] = flag
        return status

    backend = object.__new__(libdcmi.DcmiBackend)
    monkeypatch.setattr(backend, '_call', get_flag)
    assert backend.process_pid_scope() == scope


@pytest.mark.parametrize('scope', [None, 'host', 'container'])
def test_verified_initial_namespace_keeps_signal_eligibility(proc_view, scope):
    _, links, lookup = proc_view
    links['/proc/self/ns/pid'] = monitor._INITIAL_PID_NAMESPACE
    row = dict(device='5:0', pid=101, memory=128)

    monitor.BackendManager()._enrich_processes([row], scope, 100)

    lookup.assert_called_once_with(101)
    assert row['pid_namespace'] == monitor._INITIAL_PID_NAMESPACE
    assert row['created'] is not None and row['signal_allowed'] is True


@pytest.fixture
def signal_target(monkeypatch, proc_view):
    files, links, _ = proc_view
    fd = 123
    open_dir = Mock(return_value=fd)
    close_dir = Mock()
    kill = Mock()
    monkeypatch.setattr(monitor.os, 'open', open_dir)
    monkeypatch.setattr(monitor.os, 'close', close_dir)
    monkeypatch.setattr(monitor.os, 'kill', kill)
    proc = '/proc/self/fd/{}'.format(fd)
    files[proc + '/stat'] = files['/proc/101/stat']
    files[proc + '/status'] = files['/proc/101/status']
    pidfd_send_signal = Mock()
    monkeypatch.setattr(monitor.signal, 'pidfd_send_signal', pidfd_send_signal, raising=False)
    row = dict(device='5:0', pid=101, memory=128)
    return row, files, links, proc, open_dir, close_dir, kill, pidfd_send_signal


@pytest.mark.parametrize('pidfd', ['supported', 'missing', 'enosys'])
def test_signal_uses_verified_local_pid_when_procfs_uses_host_pids(
    monkeypatch, signal_target, pidfd
):
    row, files, links, proc, open_dir, close_dir, kill, pidfd_send_signal = signal_target
    files['/proc/self/status'] = 'NSpid:\t800\t5\n'
    status = 'NSpid:\t101\t42\n'
    files['/proc/101/status'] = files[proc + '/status'] = status
    links['/proc/1/ns/pid'] = monitor._INITIAL_PID_NAMESPACE
    links['/proc/101/ns/pid'] = links[proc + '/ns/pid'] = links['/proc/self/ns/pid']
    if pidfd == 'missing':
        monkeypatch.delattr(monitor.signal, 'pidfd_send_signal')
    elif pidfd == 'enosys':
        pidfd_send_signal.side_effect = OSError(errno.ENOSYS, '系统调用不可用')
    monitor.BackendManager()._enrich_processes([row], 'host', 100)
    screen = Dashboard(options())
    screen.focus = 'processes'
    frame = dict(fake_frame(), processes=[row])
    sampler = Mock(error=None)
    screen.handle(curses.KEY_HOME, frame, sampler)
    screen.handle(ord('T'), frame, sampler)
    assert screen.confirm is not None
    screen.handle(ord('y'), frame, sampler)

    assert open_dir.call_args[0][0] == '/proc/101'
    close_dir.assert_called_once()
    if pidfd != 'missing':
        pidfd_send_signal.assert_called_once_with(open_dir.return_value, signal.SIGTERM)
    if pidfd == 'supported':
        kill.assert_not_called()
    else:
        kill.assert_called_once_with(42, signal.SIGTERM)
    assert 'PID 42' in screen.notice


@pytest.mark.parametrize('change', ['snapshot', 'namespace', 'mapping', 'start-tick', 'permission'])
def test_container_signal_rechecks_identity_at_confirmation(signal_target, change):
    row, files, links, proc, open_dir, close_dir, kill, pidfd_send_signal = signal_target
    monitor.BackendManager()._enrich_processes([row], 'container', 100)
    screen = Dashboard(options())
    frame = dict(fake_frame(), processes=[row])
    sampler = Mock(error=None)
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, sampler)
    screen.handle(ord('T'), frame, sampler)
    assert screen.confirm is not None
    if change == 'snapshot':
        row['local_pid'] += 1
    elif change == 'namespace':
        links['/proc/self/ns/pid'] = 'pid:[4026549309]'
    elif change == 'mapping':
        files[proc + '/status'] = 'NSpid:\t101\t42\n'
    elif change == 'start-tick':
        files[proc + '/stat'] = files[proc + '/stat'].rsplit(' ', 1)[0] + ' 51'
    elif change == 'permission':
        pidfd_send_signal.side_effect = PermissionError(errno.EPERM, '没有权限')
    screen.handle(ord('y'), frame, sampler)
    kill.assert_not_called()
    if change == 'permission':
        pidfd_send_signal.assert_called_once()
    else:
        pidfd_send_signal.assert_not_called()
    if change in ('snapshot', 'namespace'):
        open_dir.assert_not_called()
        close_dir.assert_not_called()
    else:
        close_dir.assert_called_once_with(open_dir.return_value)


def test_container_signals_respect_readonly(signal_target):
    row, _, _, _, open_dir, _, kill, pidfd_send_signal = signal_target
    monitor.BackendManager()._enrich_processes([row], 'container', 100)
    screen = Dashboard(options(readonly=True))
    frame = dict(fake_frame(), processes=[row])
    sampler = Mock(error=None)
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, sampler)
    screen.handle(ord('T'), frame, sampler)
    assert screen.confirm is None
    screen.send_confirmed(row, frame, sampler)
    open_dir.assert_not_called()
    pidfd_send_signal.assert_not_called()
    kill.assert_not_called()
