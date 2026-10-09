"""Resize inventory integrity, whole-page navigation and clean first-frame startup."""

import curses
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop.gui.monitor import Dashboard, wait_for_frame
from nputop.gui.library.widestring import WideString
from test_monitor import options
from test_review_regressions import large_frame


@pytest.mark.parametrize('mode', ['auto', 'full', 'compact'])
@pytest.mark.parametrize('width', [80, 120, 180])
def test_resize_compacts_before_hiding_any_of_sixteen_chips(mode, width):
    frame = large_frame()
    screen = Dashboard(options(monitor=mode))
    for height in (90, 70, 60, 48, 30, 24, 48, 90):
        lines = screen.lines(frame, width, height)
        targets = {line.target for line in lines if line.target and line.target[0] == 'devices'}
        assert targets == {('devices', i) for i in range(16)}
        assert not screen.selection_active
        if height == 90:
            assert screen.row_height == (2 if mode == 'compact' else 3)
        if height <= 48:
            assert screen.row_height == 1
        if height == 60:
            assert screen.row_height == 2


def test_tiny_terminal_pages_keep_every_device_and_process_reachable():
    frame = large_frame()
    screen = Dashboard(options(monitor='full'))
    found, sampler = set(), Mock()
    for _ in range(100):
        lines = screen.lines(frame, 80, 12)
        assert screen.row_height == 1
        found.update(line.target for line in lines if line.target)
        if screen.screen_offset == screen.screen_limit:
            break
        screen.handle(curses.KEY_NPAGE, frame, sampler)
    assert found == {(kind, i) for kind in ('devices', 'processes') for i in range(16)}
    assert not screen.selection_active
    screen.handle(curses.KEY_HOME, frame, sampler)
    assert any(line.style == 'selected' for line in screen.lines(frame, 80, 12))


def test_resize_keeps_an_explicit_selection_reachable_without_selecting_on_startup():
    frame = large_frame()
    screen = Dashboard(options())
    screen.lines(frame, 180, 90)
    screen.handle(curses.KEY_END, frame, Mock())
    for width, height in [(180, 90), (80, 24), (180, 90)]:
        lines = screen.lines(frame, width, height)
        assert any(line.style == 'selected' and '1015' in line.text for line in lines)


@pytest.mark.parametrize('ascii_only', [False, True])
@pytest.mark.parametrize('mode', ['auto', 'full', 'compact'])
def test_horizontal_resize_clips_canvas_without_losing_devices(mode, ascii_only):
    frame = large_frame()
    screen = Dashboard(options(monitor=mode, ascii=ascii_only))
    # Full and compact shared-power borders used to overflow below 45/42 cols.
    # Exercise every boundary, including a one-column viewport, then restore.
    for height in (90, 60, 24):
        for width in list(range(180, 0, -1)) + [80, 120, 180]:
            lines = screen.lines(frame, width, height)
            assert len(lines) <= height
            assert all(len(WideString(line.text)) <= width - 1 for line in lines)
            assert {
                line.target for line in lines if line.target and line.target[0] == 'devices'
            } == {('devices', i) for i in range(16)}
            assert screen.row_height == (
                1 if height == 24 else 2 if height == 60 or mode == 'compact' else 3
            )
            assert not screen.selection_active and not screen.tags


@pytest.mark.parametrize('ascii_only', [False, True])
def test_narrow_canvas_preserves_merged_borders_and_selection(ascii_only):
    frame = large_frame()
    screen = Dashboard(options(monitor='full', ascii=ascii_only))
    normal = screen.lines(frame, 80, 90)
    for width in (79, 70, 45, 44, 43, 42, 41, 21, 2, 1):
        narrow = screen.lines(frame, width, 90)
        # Clipping happens after composition, including the open power cell.
        assert [line.text for line in narrow[1:]] == [
            str(WideString(line.text)[: width - 1]) for line in normal[1:]
        ]
    screen.handle(curses.KEY_END, frame, Mock())
    screen.lines(frame, 180, 90)
    selected = screen.selected_process
    for width, height in ((180, 90), (40, 60), (1, 1), (80, 24), (180, 90)):
        lines = screen.lines(frame, width, height)
        assert any(line.style == 'selected' for line in lines)
        assert screen.selection_active and screen.selected_process == selected
    assert any('1015' in line.text and line.style == 'selected' for line in lines)


def test_startup_waits_for_data_without_rendering_or_hardware_calls(monkeypatch):
    frame = large_frame()
    sampler = SimpleNamespace(frame=None, error=None, poll=Mock(side_effect=[None, frame]))
    screen = Dashboard(options())
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    monkeypatch.setattr('nputop.gui.monitor.time.sleep', lambda seconds: None)
    assert wait_for_frame(sampler, screen)
    assert sampler.poll.call_count == 2 and screen.rendered == []


def test_startup_error_is_visible_and_retryable_without_loading_splash(monkeypatch):
    sampler = SimpleNamespace(frame=None, error='driver unavailable', poll=Mock(return_value=None))
    screen = Dashboard(options())
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    assert wait_for_frame(sampler, screen)
    text = '\n'.join(line.text for line in screen.lines(None, 80, 24, sampler.error))
    assert 'driver unavailable' in text and 'r retry' in text and 'q quit' in text
    assert 'Collecting' not in text and 'separate process' not in text


@pytest.mark.parametrize('key', [b'q', b'\x03'])
def test_startup_can_cancel_and_restores_terminal(monkeypatch, key):
    import termios
    import tty

    screen = Dashboard(options())
    sampler = SimpleNamespace(frame=None, error=None, poll=Mock(return_value=None))
    monkeypatch.setattr('sys.stdin', SimpleNamespace(isatty=lambda: True, fileno=lambda: 123))
    monkeypatch.setattr(termios, 'tcgetattr', lambda fd: ['original'])
    restore = Mock()
    monkeypatch.setattr(termios, 'tcsetattr', restore)
    monkeypatch.setattr(tty, 'setcbreak', Mock())
    monkeypatch.setattr('select.select', lambda *args: ([123], [], []))
    monkeypatch.setattr('os.read', lambda *args: key)
    if key == b'\x03':
        with pytest.raises(KeyboardInterrupt):
            wait_for_frame(sampler, screen)
    else:
        assert wait_for_frame(sampler, screen) is False
    restore.assert_called_once_with(123, termios.TCSANOW, ['original'])
