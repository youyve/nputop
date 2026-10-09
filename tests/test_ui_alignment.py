"""Migration UI behavior and layout without device queries or workload signals."""

import curses
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop.gui.monitor import Dashboard, elapsed
from nputop.gui.library.widestring import WideString
from test_monitor import fake_frame, interaction_frame, options
from test_review_regressions import large_frame


def test_load_bars_and_history_use_independent_colors():
    frame = fake_frame()
    metrics = frame['devices'][0]['metrics']
    metrics['aicore'].update(value=98)
    metrics['memory_used'].update(value=100)
    metrics['npu_overall'] = dict(value=95, state='ok')
    lines = Dashboard(options(ascii=False)).lines(frame, 200, 60)
    devices = [line for line in lines if line.target == ('devices', 0)]
    assert [span[2] for line in devices for span in line.spans] == [
        'device',
        'muted',
        'high',
        'muted',
    ]
    assert all(line.style == 'high' for line in devices)
    header = next(line.text for line in lines if 'Memory used / total' in line.text)
    assert header.rfind('│') == 79
    cpu = next(line for line in lines if ' CPU ' in line.text)
    assert cpu.text[79] == '│'
    assert cpu.style == 'cpu'
    graphs = [line for line in lines if line.style == 'chart']
    assert graphs and all(line.spans[0][2] == 'high' for line in graphs)


@pytest.mark.parametrize('width,height', [(80, 24), (120, 30), (160, 48), (220, 80)])
@pytest.mark.parametrize('mode', ['auto', 'compact', 'full'])
def test_selected_process_remains_visible_at_supported_sizes(width, height, mode):
    frame = large_frame()
    screen = Dashboard(options(monitor=mode, ascii=False))
    screen.lines(frame, width, height)
    screen.handle(curses.KEY_END, frame, Mock())
    lines = screen.lines(frame, width, height)
    selected = [line for line in lines if line.style == 'selected']
    assert len(selected) == 1 and '1015' in selected[0].text
    assert all(len(WideString(line.text)) <= width - 1 for line in lines)
    # Small viewports scroll to the process instead of removing other devices
    # from the canvas. Returning to the inventory retains the linked highlight.
    while screen.screen_offset:
        screen.handle(curses.KEY_PPAGE, frame, Mock())
        lines = screen.lines(frame, width, height)
    assert any('7:1' in line.text for line in lines if line.emphasis == 'linked')


def test_navigation_sorting_and_display_shortcuts():
    frame = interaction_frame()
    screen = Dashboard(options())
    sampler = Mock()
    screen.handle(9, frame, sampler)
    assert screen.process_index == 0 and screen.selection_active
    screen.handle(9, frame, sampler)
    assert screen.process_index == 1 and screen.focus == 'processes'
    screen.handle(curses.KEY_BTAB, frame, sampler)
    assert screen.process_index == 0
    screen.handle(curses.KEY_END, frame, sampler)
    assert screen.process_index == 2
    screen.handle(curses.KEY_HOME, frame, sampler)
    assert screen.process_index == 0
    before = [p['pid'] for p in screen.processes(frame)]
    screen.handle(ord('/'), frame, sampler)
    assert [p['pid'] for p in screen.processes(frame)] == [before[1], before[0], before[2]]
    screen.handle(ord('.'), frame, sampler)
    assert screen.sort == 'cpu'
    screen.handle(ord(','), frame, sampler)
    assert screen.sort == 'memory'
    for key, mode in [('f', 'full'), ('c', 'compact'), ('a', 'auto')]:
        screen.handle(ord(key), frame, sampler)
        assert screen.display_mode == mode and not screen.expanded
    screen.handle(ord('d'), frame, sampler)
    assert screen.expanded
    for key in (18, curses.KEY_F5):
        screen.handle(key, frame, sampler)
    assert sampler.refresh.call_count == 2


def test_command_scrolling_keeps_identity_and_unicode_columns():
    frame = interaction_frame()
    for row in frame['processes']:
        row.update(command='START ' + '数据' * 100 + ' END', user='用户', time=3661)
    screen = Dashboard(options(ascii=False))
    screen.handle(curses.KEY_HOME, frame, Mock())
    lines = screen.lines(frame, 100, 40)
    first = next(line.text for line in lines if line.style == 'selected')
    assert '1:01:01' in first
    screen.handle(ord('$'), frame, Mock())
    last = next(line.text for line in screen.lines(frame, 100, 40) if line.style == 'selected')
    assert 'END' in last and 'START' not in last
    assert str(WideString(first)[:66]) == str(WideString(last)[:66])
    screen.handle(curses.KEY_LEFT, frame, Mock())
    assert screen.command_offset == screen.command_limit - 8
    screen.handle(ord('^'), frame, Mock())
    assert screen.command_offset == 0


@pytest.mark.parametrize(
    'key,action', [('k', 'SIGKILL'), ('T', 'SIGTERM'), ('I', 'SIGINT'), ('K', 'SIGKILL')]
)
def test_signal_keys_display_exact_action_and_require_confirmation(monkeypatch, key, action):
    frame = interaction_frame()
    for row in frame['processes']:
        row.update(signal_allowed=True, pid_namespace='test')
    screen = Dashboard(options())
    process = Mock()
    process.create_time.return_value = 3  # highest-memory process on the first chip
    lookup = Mock(return_value=process)
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', lookup)
    monkeypatch.setattr('nputop.gui.actions.host_pid_namespace', lambda: 'test')
    sampler = SimpleNamespace(error=None)
    screen.handle(curses.KEY_HOME, frame, sampler)
    screen.handle(ord(key), frame, sampler)
    assert action in '\n'.join(line.text for line in screen.lines(frame, 160, 60))
    process.send_signal.assert_not_called()
    screen.handle(-1, frame, sampler)
    screen.handle(ord('y'), frame, sampler)
    process.send_signal.assert_called_once_with(getattr(signal, action))


def test_readonly_and_help_block_all_signal_shortcuts(monkeypatch):
    frame = interaction_frame()
    for row in frame['processes']:
        row.update(signal_allowed=True, pid_namespace='test')
    lookup = Mock()
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', lookup)
    for readonly, help_screen in [(True, False), (False, True)]:
        screen = Dashboard(options(readonly=readonly))
        screen.handle(curses.KEY_HOME, frame, Mock())
        screen.help = help_screen
        for key in 'kTIK':
            screen.handle(ord(key), frame, Mock())
            assert screen.confirm is None
            screen.handle(ord('y'), frame, Mock())
    lookup.assert_not_called()


def test_elapsed_duration_preserves_unknown_and_long_jobs():
    assert elapsed(None) == '--'
    assert elapsed(0) == '0:00:00'
    assert elapsed(90061) == '1d 01:01'
