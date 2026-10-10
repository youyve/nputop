"""Behavioral regression checks for the complete snapshot UI migration."""

import curses
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop import preview
from nputop.api.inspection import identity
from nputop.gui.monitor import Dashboard
from nputop.gui.library.widestring import WideString
from test_monitor import options, interaction_frame


def frame():
    value = interaction_frame()
    for p in value['processes']:
        p.update(pid_namespace='host', signal_allowed=True, user='tester', cpu=5, host_rss=2048)
    return value


def test_selection_clear_disappearance_and_cross_chip_tags():
    value = frame()
    duplicate = dict(value['processes'][-1], device='8:0')
    value['processes'].append(duplicate)
    screen = Dashboard(options())
    screen.lines(value, 160, 60)
    sampler = SimpleNamespace(error=None)
    screen.handle(curses.KEY_HOME, value, sampler)
    screen.handle(ord(' '), value, sampler)
    screen.handle(ord('k'), value, sampler)
    assert len(screen.confirm['_targets']) == 1
    assert screen.confirm['_signal'] == 'SIGKILL'
    screen.handle(27, value, sampler)
    screen.handle(27, value, sampler)
    screen.lines(value, 160, 60)
    screen.handle(3, value, sampler)
    assert not screen.selection_active and not screen.tags and screen.confirm is None
    screen.handle(curses.KEY_HOME, value, sampler)
    screen.lines(value, 160, 60)
    value['processes'] = [p for p in value['processes'] if p['pid'] != 77]
    screen.lines(value, 160, 60)
    screen.handle(ord('k'), value, sampler)
    assert screen.confirm is None


def test_batch_deduplicates_and_revalidates_each_pid(monkeypatch):
    value = frame()
    screen = Dashboard(options())
    screen.tags = {identity(p): p for p in value['processes']}
    processes = {p['pid']: Mock() for p in value['processes']}
    for p in value['processes']:
        processes[p['pid']].create_time.return_value = p['created']
    processes[88].create_time.return_value = 100  # reused after confirmation
    monkeypatch.setattr('nputop.gui.actions.host_pid_namespace', lambda: 'host')
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', lambda pid: processes[pid])
    screen.handle(ord('T'), value, SimpleNamespace(error=None))
    screen.handle(ord('y'), value, SimpleNamespace(error=None))
    processes[77].send_signal.assert_called_once_with(signal.SIGTERM)
    processes[99].send_signal.assert_called_once_with(signal.SIGTERM)
    processes[88].send_signal.assert_not_called()
    assert any('reused' in result for result in screen.signal_results)
    assert len(screen.signal_results) == 3


@pytest.mark.parametrize('view,key', [('tree', 't'), ('environ', 'e'), ('metrics', '\n')])
def test_pages_request_on_demand_and_preserve_namespace(view, key):
    value = frame()
    screen = Dashboard(options())
    assert screen.inspect_request(value) == (None, None)
    screen.handle(curses.KEY_HOME, value, Mock())
    screen.handle(ord(key), value, Mock())
    token, request = screen.inspect_request(value)
    assert screen.view == view and request[1]['pid'] == 77
    screen.handle(ord('r'), value, Mock())
    assert screen.inspect_request(value)[0] != token
    screen.inspection = dict(state='unavailable', reason='NoSuchProcess: exited')
    assert 'exited' in '\n'.join(l.text for l in screen.lines(value, 80, 24))
    screen.handle(27, value, Mock())
    assert screen.inspect_request(value) == (None, None)


def test_environment_horizontal_vertical_scroll_and_help_is_modal():
    value = frame()
    screen = Dashboard(options(ascii=False))
    screen.handle(curses.KEY_HOME, value, Mock())
    screen.handle(ord('e'), value, Mock())
    screen.inspection = dict(
        state='ok', entries=['VAR' + str(i) + '=' + '数据' * 100 + ' END' for i in range(100)]
    )
    screen.lines(value, 80, 24)
    screen.handle(ord('$'), value, Mock())
    screen.handle(curses.KEY_END, value, Mock())
    lines = screen.lines(value, 80, 24)
    assert any('END' in l.text for l in lines)
    assert screen.view_offset > 0
    assert all(len(WideString(l.text)) <= 79 for l in lines)
    screen.handle(ord('h'), value, Mock())
    screen.handle(ord('k'), value, Mock())
    screen.handle(ord('y'), value, Mock())
    assert screen.confirm is None
    screen.handle(ord('q'), value, Mock())
    assert not screen.help and screen.view == 'environ'


def test_selected_scope_changes_only_device_history():
    value = frame()
    screen = Dashboard(options())
    screen.handle(curses.KEY_HOME, value, Mock())
    screen.lines(value, 180, 70)
    assert screen._history_selection == ('5:0',)
    assert len(screen.history['@time']) == 1
    screen.handle(27, value, Mock())
    screen.lines(value, 180, 70)
    assert screen._history_selection == ('5:0', '8:0')
    assert len(screen.history['@time']) == 1
    assert len(screen.history['@device_time']) == 1


def test_sort_prefix_alt_navigation_and_no_accidental_signal():
    value = frame()
    screen = Dashboard(options())
    for char in 'op':
        screen.handle(ord(char), value, Mock())
    assert screen.sort == 'pid' and screen.sort_reverse
    for char in 'oP':
        screen.handle(ord(char), value, Mock())
    assert not screen.sort_reverse
    screen.feed(27, value, Mock())
    screen.feed(ord('j'), value, Mock())
    assert screen.process_index == 0 and screen.selection_active
    screen.feed(27, value, Mock())
    screen.feed(ord('j'), value, Mock())
    assert screen.process_index == 1
    screen.feed(27, value, Mock())
    screen.escape_at -= 1
    screen.feed(-1, value, Mock())
    assert not screen.selection_active


def test_process_memory_and_cpu_are_not_multiplied_by_chip_count():
    value = frame()
    value['processes'].append(dict(value['processes'][-1], device='8:0'))
    screen = Dashboard(options())
    screen.handle(curses.KEY_HOME, value, Mock())
    screen.open_view('metrics', value)
    screen.inspection = dict(state='ok', ppid=1, threads=4, status='running')
    screen.lines(value, 100, 40)
    assert list(screen.process_history['cpu']) == [5]
    assert list(screen.process_history['rss']) == [2048]
    assert list(screen.process_history['npu']) == [400]
    value['sampled_at'] += 1
    value['processes'] = []
    screen.lines(value, 100, 40)
    assert screen.process_history['npu'][-1] is None


def test_explicit_visibility_is_not_a_display_index():
    value = frame()
    for device, logical in zip(value['devices'], [2, 9]):
        device['logical_id'] = logical
    screen = Dashboard(options(visible_ids=[9]))
    assert [d['key'] for d in screen.devices(value)] == ['8:0']
    assert screen.selected_key(None) == '@mapping'
    screen.expanded = True
    assert screen.selected_key(value) == '8:0'
    value['devices'][0]['logical_id'] = None
    assert screen.devices(value) == []


def test_cli_compatibility_and_precedence(monkeypatch):
    monkeypatch.setenv('nputop_MONITOR_MODE', 'full,light,colorful')
    monkeypatch.setenv('nputop_NPU_UTILIZATION_THRESHOLDS', '20,90')
    monkeypatch.setenv('ASCEND_RT_VISIBLE_DEVICES', '2,9')
    args = preview.parse_arguments(['--only-visible'])
    assert args.interval == 1 and args.light and args.colorful and args.monitor == 'full'
    assert args.npu_util_thresh == [20, 90] and args.visible_ids == [2, 9]
    args = preview.parse_arguments(
        ['-m', 'compact', '--npu-util-thresh', '5', '60', '-o', '0', '-ov']
    )
    assert (
        args.monitor == 'compact' and args.npu_util_thresh == [5, 60] and args.visible_ids is None
    )
    for command in (['--compute'], ['--mem-util-thresh', '80', '20']):
        with pytest.raises(SystemExit) as error:
            preview.parse_arguments(command)
        assert error.value.code == 2


def test_device_and_host_history_continue_while_process_page_is_open():
    value = frame()
    screen = Dashboard(options())
    screen.lines(value, 160, 60)
    screen.handle(curses.KEY_HOME, value, Mock())
    screen.open_view('environ', value)
    assert screen.view == 'environ'
    screen.inspection = dict(state='ok', entries=['TEST=value'])
    for _ in range(3):
        value['sampled_at'] += 1
        screen.lines(value, 160, 60)
    screen.handle(27, value, Mock())
    screen.lines(value, 160, 60)
    assert len(screen.history['@time']) == 4
    assert len(screen.history['@device_time']) == 4


def test_runtime_visibility_stops_at_first_invalid_id():
    value = frame()
    for device, logical in zip(value['devices'], [2, 9]):
        device['logical_id'] = logical
    screen = Dashboard(options(visible_ids=[2, 3, 9]))
    assert [d['logical_id'] for d in screen.devices(value)] == [2]
    assert 'ID 3 unavailable' in screen.notice


def test_legacy_driver_error_does_not_reference_missing_nvml_exceptions(monkeypatch, capsys):
    import sys
    from nputop import cli

    monkeypatch.setattr(sys, 'argv', ['nputop', '--legacy-ui', '--once'])
    monkeypatch.setattr(cli, 'setlocale_utf8', lambda: True)
    monkeypatch.setattr(cli.Device, 'count', Mock(side_effect=FileNotFoundError('driver missing')))
    assert cli.main() == 1
    assert 'NPU ERROR:' in capsys.readouterr().err


def test_json_preserves_unfiltered_order_and_applies_explicit_filters(monkeypatch, capsys):
    import json

    value = frame()
    sampler = Mock()
    sampler.poll.return_value = value
    monkeypatch.setattr(preview, 'Sampler', lambda *args: sampler)
    assert preview.main(['--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert [p['pid'] for p in result['processes']] == [p['pid'] for p in value['processes']]
    assert preview.main(['--json', '--only', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert [d['id'] for d in result['devices']] == [1]
    assert all(p['device'] == '8:0' for p in result['processes'])


def test_natural_sort_reverses_chip_groups_and_pids_together():
    value = frame()
    screen = Dashboard(options())
    for char in 'on':
        screen.handle(ord(char), value, Mock())
    forward = [(p['device'], p['pid']) for p in screen.processes(value)]
    for char in 'oN':
        screen.handle(ord(char), value, Mock())
    assert [(p['device'], p['pid']) for p in screen.processes(value)] == forward[::-1]


def test_cleared_process_selection_keeps_expanded_device_stable():
    value = frame()
    screen = Dashboard(options())
    screen.lines(value, 160, 60)
    screen.handle(curses.KEY_END, value, Mock())
    screen.lines(value, 160, 60)
    screen.expanded = True
    assert screen.selected_key(value) == '8:0'
    screen.handle(27, value, Mock())
    value['processes'] = [p for p in value['processes'] if p['device'] == '5:0']
    assert screen.selected_key(value) == '8:0'
