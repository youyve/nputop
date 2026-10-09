"""Idle redraw limits must not delay data, input, resize or Escape handling."""

import copy
import curses
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop.gui import monitor as ui
from test_monitor import interaction_frame, options


def run_loop(monkeypatch, ticks=40, event=None, selected=False):
    clock = [100.0]
    frame = interaction_frame()
    frame['sampled_at'] = clock[0]
    args = options()
    screen = ui.Dashboard(args)
    sampler = SimpleNamespace(frame=frame, error=None)
    sampler.poll = Mock(side_effect=lambda detail: sampler.frame)
    inspector = SimpleNamespace(result=None, close=Mock())
    inspector.poll = Mock(side_effect=lambda *args: inspector.result)
    window = Mock()
    window.getmaxyx.return_value = (48, 160)
    draws = []
    original = screen.lines

    def lines(*args):
        value = original(*args)
        draws.append((clock[0], screen.selection_active))
        return value

    screen.lines = lines
    if selected:
        screen.handle(curses.KEY_DOWN, frame, sampler)
    step = [0]

    def getch():
        step[0] += 1
        clock[0] = 100 + step[0] * 0.025
        if step[0] >= ticks:
            return ord('q')
        return event(step[0], sampler, inspector, window) if event else -1

    window.getch.side_effect = getch
    monkeypatch.setattr(ui.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(ui, 'wait_for_frame', lambda *args: True)
    monkeypatch.setattr(ui, 'Dashboard', lambda args: screen)
    monkeypatch.setattr(ui, 'Inspector', lambda: inspector)
    monkeypatch.setattr(ui, 'draw_line', Mock())
    monkeypatch.setattr(ui.curses, 'wrapper', lambda loop: loop(window))
    monkeypatch.setattr(ui.curses, 'has_colors', lambda: False)
    for name in ('curs_set', 'mousemask', 'mouseinterval', 'raw', 'doupdate'):
        monkeypatch.setattr(ui.curses, name, Mock())
    if hasattr(ui.curses, 'set_escdelay'):
        monkeypatch.setattr(ui.curses, 'set_escdelay', Mock())
    ui.run_dashboard(sampler, args)
    inspector.close.assert_called_once()
    window.timeout.assert_called_once_with(25)
    return draws, sampler


def test_idle_redraws_are_bounded_without_slowing_collection_polling(monkeypatch):
    draws, sampler = run_loop(monkeypatch)
    assert 4 <= len(draws) <= 5
    assert sampler.poll.call_count == 40


@pytest.mark.parametrize('kind', ['frame', 'inspection', 'error', 'resize', 'key'])
def test_changes_redraw_before_the_idle_deadline(monkeypatch, kind):
    def event(step, sampler, inspector, window):
        if step == 2:
            if kind == 'frame':
                sampler.frame = copy.deepcopy(sampler.frame)
                sampler.frame['sampled_at'] += 0.05
            elif kind == 'inspection':
                inspector.result = {'state': 'unavailable', 'reason': 'test response'}
            elif kind == 'error':
                sampler.error = 'test collection failure'
            elif kind == 'resize':
                window.getmaxyx.return_value = (24, 80)
            elif kind == 'key':
                return curses.KEY_DOWN
        return -1

    draws, _ = run_loop(monkeypatch, ticks=8, event=event)
    assert len(draws) == 2
    assert draws[1][0] == pytest.approx(100.05)


def test_standalone_escape_clears_selection_without_waiting_for_idle_redraw(monkeypatch):
    def event(step, *args):
        return 27 if step == 1 else -1

    draws, _ = run_loop(monkeypatch, ticks=8, event=event, selected=True)
    assert draws[0][1] is True
    cleared = [when for when, selected in draws if not selected]
    assert cleared and cleared[0] <= 100.1
