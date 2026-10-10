"""Signal confirmation is an exclusive overlay over the current viewport."""

import curses
import copy
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop.api.inspection import identity
from nputop.gui.monitor import Dashboard, draw_line
from nputop.gui.library.widestring import WideString
from test_full_migration import frame
from test_monitor import options


def open_dialog(ascii_only=False, width=80, height=24):
    value = frame()
    screen = Dashboard(options(ascii=ascii_only))
    sampler = SimpleNamespace(error=None)
    screen.lines(value, width, height)
    screen.handle(curses.KEY_HOME, value, sampler)
    screen.lines(value, width, height)
    screen.handle(ord('k'), value, sampler)
    screen.lines(value, width, height)
    return screen, value, sampler


@pytest.mark.parametrize('ascii_only', [False, True])
@pytest.mark.parametrize('width,height', [(80, 24), (180, 60), (40, 12)])
def test_centered_overlay_keeps_viewport_selection_and_button_hitboxes(ascii_only, width, height):
    screen, value, sampler = open_dialog(ascii_only, width, height)
    background = screen.rendered
    selection, offset = screen.selected_process, screen.screen_offset
    assert any(line.target for line in background)
    assert not any('Send SIGKILL' in line.text for line in background)
    assert 'Send SIGKILL?' in '\n'.join(line.text for line in screen.dialog_lines)
    x, y = screen.dialog_position
    box_width = len(WideString(screen.dialog_lines[0].text))
    assert x == (width - 1 - box_width) // 2
    assert y == (height - len(screen.dialog_lines)) // 2
    for left, top, span, rows, action in screen.dialog_buttons:
        label = str(WideString(screen.dialog_lines[top - y + 1].text)[left - x:left - x + span])
        assert ('Confirm (y)' if action else 'Cancel (n)') in label
        assert x <= left < left + span <= x + box_width
        assert y <= top < top + rows < y + len(screen.dialog_lines)
    if ascii_only:
        '\n'.join(line.text for line in screen.dialog_lines).encode('ascii')
    screen.handle(27, value, sampler)
    screen.lines(value, width, height)
    assert screen.selected_process == selection and screen.selection_active
    assert screen.screen_offset == offset and not screen.dialog_lines


def test_dialog_input_is_exclusive_and_live_sampling_preserves_frozen_targets():
    screen, value, sampler = open_dialog()
    before = (screen.selected_process, screen.screen_offset, screen.command_offset, screen.sort)
    target = copy.deepcopy(screen.confirm)
    for key in ('s', 'r', 'f', 't', 'e', ' ', 'k', 'T', 'h'):
        screen.handle(ord(key), value, sampler)
    screen.mouse(0, 0, curses.BUTTON1_PRESSED, value, sampler)
    screen.mouse(0, 0, curses.BUTTON1_RELEASED, value, sampler)
    assert screen.confirm == target
    assert not screen.tags and screen.view == 'main' and not screen.help
    assert before == (screen.selected_process, screen.screen_offset, screen.command_offset, screen.sort)
    history = len(screen.history['@time'])
    value['sampled_at'] += 1
    value['host']['cpu'] = 37
    screen.lines(value, 80, 24)
    assert len(screen.history['@time']) == history + 1
    assert screen.history['@cpu'][-1] == 37 and screen.confirm == target


@pytest.mark.parametrize('key', [9, curses.KEY_BTAB, curses.KEY_LEFT, curses.KEY_RIGHT])
def test_button_navigation_and_enter_cancel_do_not_touch_main_selection(key):
    screen, value, sampler = open_dialog()
    selected = screen.selected_process
    screen.send_confirmed = Mock()
    screen.handle(key, value, sampler)
    assert screen.confirm_choice is False
    screen.handle(curses.KEY_ENTER, value, sampler)
    assert screen.confirm is None and screen.selected_process == selected
    screen.send_confirmed.assert_not_called()


@pytest.mark.parametrize('action', [True, False])
def test_mouse_press_release_and_click_route_through_existing_confirmation(action):
    for click in (False, True):
        screen, value, sampler = open_dialog()
        original = screen.confirm
        screen.send_confirmed = Mock()
        left, top, _, _, _ = next(b for b in screen.dialog_buttons if b[-1] is action)
        if click:
            screen.mouse(left + 2, top + 1, curses.BUTTON1_CLICKED, value, sampler)
        else:
            screen.mouse(left + 2, top + 1, curses.BUTTON1_PRESSED, value, sampler)
            assert screen.confirm is original
            screen.send_confirmed.assert_not_called()
            screen.mouse(left + 2, top + 1, curses.BUTTON1_RELEASED, value, sampler)
        assert screen.confirm is None
        if action:
            screen.send_confirmed.assert_called_once_with(original, value, sampler)
        else:
            screen.send_confirmed.assert_not_called()


def test_mouse_drag_or_resize_between_press_and_release_cannot_confirm():
    for resize in (False, True):
        screen, value, sampler = open_dialog()
        screen.send_confirmed = Mock()
        left, top, _, _, _ = screen.dialog_buttons[0]
        screen.mouse(left + 2, top + 1, curses.BUTTON1_PRESSED, value, sampler)
        if resize:
            screen.handle(curses.KEY_RESIZE, value, sampler)
            screen.lines(value, 180, 60)
            left, top, _, _, _ = screen.dialog_buttons[0]
        else:
            left, top = -2, -1
        screen.mouse(left + 2, top + 1, curses.BUTTON1_RELEASED, value, sampler)
        assert screen.confirm
        screen.send_confirmed.assert_not_called()


def test_batch_scroll_is_bounded_to_popup_and_cancel_preserves_tags():
    screen, value, sampler = open_dialog()
    screen.handle(27, value, sampler)
    rows = [dict(value['processes'][0], pid=100 + i) for i in range(30)]
    screen.tags = {identity(p): p for p in rows}
    screen.handle(ord('T'), value, sampler)
    screen.lines(value, 80, 24)
    offset, selected = screen.screen_offset, screen.selected_process
    assert screen.modal_page_size == 6
    screen.handle(curses.KEY_NPAGE, value, sampler)
    assert screen.modal_offset == 6
    screen.handle(curses.KEY_END, value, sampler)
    screen.lines(value, 80, 24)
    assert 'PID 129' in '\n'.join(line.text for line in screen.dialog_lines)
    screen.mouse(0, 0, curses.BUTTON4_PRESSED, value, sampler)
    assert screen.modal_offset == 23
    screen.handle(27, value, sampler)
    assert len(screen.tags) == 30
    assert screen.screen_offset == offset and screen.selected_process == selected


@pytest.mark.parametrize('view', ['metrics', 'environ'])
def test_cancel_restores_originating_process_page(view):
    screen, value, sampler = open_dialog()
    screen.handle(27, value, sampler)
    screen.open_view(view, value)
    target, generation = screen.view_target, screen.view_generation
    screen.lines(value, 80, 24)
    screen.handle(ord('k'), value, sampler)
    screen.lines(value, 80, 24)
    assert screen.dialog_lines and screen.view == view
    screen.handle(27, value, sampler)
    screen.lines(value, 80, 24)
    assert screen.view == view and screen.view_target == target
    assert screen.view_generation == generation


@pytest.mark.parametrize('ascii_only', [False, True])
def test_tiny_terminal_blocks_confirmation_and_recovers_without_losing_target(ascii_only):
    screen, value, sampler = open_dialog(ascii_only)
    target = screen.confirm
    screen.send_confirmed = Mock()
    for width, height in [(0, 0), (1, 1), (20, 8), (35, 24), (80, 11), (80, 2)]:
        screen.handle(curses.KEY_RESIZE, value, sampler)
        screen.lines(value, width, height)
        assert not screen.confirm_visible
        assert len(screen.dialog_lines) <= height
        assert all(len(WideString(line.text)) <= max(0, width - 1) for line in screen.dialog_lines)
        screen.handle(ord('y'), value, sampler)
        screen.handle(curses.KEY_ENTER, value, sampler)
        assert screen.confirm is target
        screen.send_confirmed.assert_not_called()
    screen.lines(value, 80, 24)
    assert screen.confirm_visible and screen.confirm is target
    screen.handle(ord('n'), value, sampler)
    assert screen.confirm is None


def test_wide_unicode_labels_and_offset_rendering_stay_inside_dialog():
    screen, value, sampler = open_dialog()
    screen.confirm['_targets'][0]['user'] = '测试用户' * 40
    screen.lines(value, 80, 24)
    x, y = screen.dialog_position
    width = len(WideString(screen.dialog_lines[0].text))
    styles = dict(normal=0, border=0, warning=curses.A_BOLD, muted=curses.A_DIM, linked=curses.A_BOLD)
    window = Mock()
    for i, line in enumerate(screen.dialog_lines):
        assert len(WideString(line.text)) == width
        draw_line(window, y + i, line, 80, styles, x=x)
    for call in window.addstr.call_args_list:
        row, col, text, _ = call[0]
        assert y <= row < y + len(screen.dialog_lines)
        assert x <= col < col + len(WideString(text)) <= x + width


@pytest.mark.parametrize('activation', ['mouse', 'enter'])
@pytest.mark.parametrize('state', ['ok', 'stale', 'reused', 'readonly', 'backend-error'])
def test_new_confirmation_controls_keep_signal_guards(monkeypatch, activation, state):
    screen, value, sampler = open_dialog()
    process = Mock()
    process.create_time.return_value = screen.confirm['created']
    monkeypatch.setattr('nputop.gui.actions.psutil.Process', lambda pid: process)
    monkeypatch.setattr('nputop.gui.actions.host_pid_namespace', lambda: 'host')
    if state == 'stale':
        value['sampled_at'] -= 20
    elif state == 'reused':
        process.create_time.return_value += 100
    elif state == 'readonly':
        screen.args.readonly = True
    elif state == 'backend-error':
        sampler.error = 'collection failed'
    if activation == 'enter':
        screen.handle(curses.KEY_ENTER, value, sampler)
    else:
        left, top, _, _, _ = screen.dialog_buttons[0]
        screen.mouse(left + 2, top + 1, curses.BUTTON1_CLICKED, value, sampler)
    assert screen.confirm is None
    if state == 'ok':
        process.send_signal.assert_called_once_with(signal.SIGKILL)
    else:
        process.send_signal.assert_not_called()
