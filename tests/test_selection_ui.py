"""Selection brightness, hit testing and cancellation against the rendered UI."""

import copy
import curses
from unittest.mock import Mock

import pytest

from nputop.gui.monitor import Dashboard, draw_line
from test_full_migration import frame
from test_monitor import options


@pytest.mark.parametrize('mode', ['auto', 'full', 'compact'])
@pytest.mark.parametrize('ascii_only', [False, True])
def test_startup_and_refresh_leave_processes_unselected(mode, ascii_only):
    value = frame()
    screen = Dashboard(options(monitor=mode, ascii=ascii_only))
    sampler = Mock()
    screen.lines(None, 180, 60)
    empty = copy.deepcopy(value)
    empty['processes'] = []
    screen.lines(empty, 180, 60)
    for key in (-1, curses.KEY_RESIZE, ord('/'), ord('f'), ord('c'), ord('r')):
        value['sampled_at'] += 1
        screen.handle(key, value, sampler)
        lines = screen.lines(value, 180, 60)
        assert not screen.selection_active and screen.selected_process is None
        assert screen.chosen_process(value) is None and not screen.tags
        assert all(line.style != 'selected' and not line.emphasis for line in lines)
        assert screen._history_selection == ('5:0', '8:0')
    for key in ('k', 'K', 'T', 'I', '\x03', ' ', 't', 'e', '\n', 'y'):
        screen.handle(ord(key), value, sampler)
        assert screen.confirm is None and not screen.tags and screen.view == 'main'
        assert screen.inspect_request(value) == (None, None)


@pytest.mark.parametrize(
    'key,index',
    [
        (curses.KEY_DOWN, 0),
        (curses.KEY_UP, 2),
        (curses.KEY_HOME, 0),
        (curses.KEY_END, 2),
        (9, 0),
        (curses.KEY_BTAB, 2),
    ],
)
def test_first_navigation_selects_endpoint_only_when_rows_exist(key, index):
    value = frame()
    screen = Dashboard(options())
    empty = copy.deepcopy(value)
    empty['processes'] = []
    screen.handle(key, empty, Mock())
    screen.lines(value, 180, 60)
    assert screen.chosen_process(value) is None
    for _ in range(2):  # Same behavior at startup and after explicit cancellation.
        screen.handle(key, value, Mock())
        assert screen.selection_active and screen.process_index == index
        assert screen.chosen_process(value) == screen.processes(value)[index]
        screen.lines(value, 180, 60)
        screen.handle(27, value, Mock())


@pytest.mark.parametrize('ascii_only', [False, True])
@pytest.mark.parametrize('mode', ['full', 'compact'])
@pytest.mark.parametrize('width', [80, 180])
def test_device_selection_preserves_load_colors_and_background(ascii_only, mode, width):
    value = frame()
    # One process on two chips, with a separate unrelated card. No fixed chip count.
    chip = copy.deepcopy(value['devices'][0])
    chip.update(id=2, key='5:1', chip=1)
    value['devices'].append(chip)
    value['processes'].append(dict(value['processes'][-1], device='5:1'))
    for row in value['devices']:
        row['metrics']['memory_used']['value'] = 100
        row['metrics']['aicore']['value'] = 98
        row['metrics']['npu_overall'] = dict(value=95, state='ok')
    screen = Dashboard(options(ascii=ascii_only, monitor=mode))
    screen.handle(curses.KEY_HOME, value, Mock())
    styles = dict(
        normal=0,
        border=0,
        device=256,
        high=512 | curses.A_BOLD,
        linked=curses.A_BOLD,
        muted=curses.A_DIM,
    )
    lines = screen.lines(value, width, 60)
    devices = [line for line in lines if line.target and line.target[0] == 'devices']
    assert {line.target[1] for line in devices} == {0, 1, 2}
    for line in devices:
        window = Mock()
        draw_line(window, 0, line, width, styles)
        linked = screen.devices(value)[line.target[1]]['key'] in ('5:0', '5:1')
        attr = window.addstr.call_args_list[0][0][-1]
        assert not attr & curses.A_REVERSE
        assert bool(attr & curses.A_BOLD) == linked
        assert bool(attr & curses.A_DIM) != linked
        for i, span in enumerate(line.spans, start=1):
            color = span[2]
            attr = window.addstr.call_args_list[i][0][-1]
            assert attr & curses.A_COLOR == styles[color] & curses.A_COLOR
            assert not attr & curses.A_REVERSE
            assert bool(attr & curses.A_DIM) != linked
        if width == 180 and 'MEM ' in line.text:
            assert [s[2] for s in line.spans] == (
                ['device', 'muted'] if mode == 'full' else ['device', 'high']
            )
        # Table borders retain their neutral appearance, including ASCII.
        assert window.addstr.call_args_list[-1][0][-1] == 0
    screen.mouse(3, 0, curses.BUTTON1_PRESSED, value, Mock())
    restored = screen.lines(value, width, 60)
    assert all(not line.emphasis for line in restored)
    assert set(screen._history_selection) == {'5:0', '5:1', '8:0'}


@pytest.mark.parametrize('ascii_only', [False, True])
def test_clicking_any_nonprocess_region_clears_selection_tags_and_signal_target(ascii_only):
    value = frame()
    screen = Dashboard(options(ascii=ascii_only))
    sampler = Mock()
    lines = screen.lines(value, 180, 60)
    process_y = next(i for i, line in enumerate(lines) if line.target == ('processes', 0))
    positions = [
        (4, i) for i, line in enumerate(lines) if not line.target or line.target[0] != 'processes'
    ]
    positions += [(0, process_y), (178, process_y), (179, process_y), (4, 59)]
    for x, y in positions:
        screen.mouse(4, process_y, curses.BUTTON1_PRESSED, value, sampler)
        screen.handle(ord(' '), value, sampler)
        assert screen.tags
        screen.mouse(x, y, curses.BUTTON1_PRESSED, value, sampler)
        screen.lines(value, 180, 60)
        assert screen.chosen_process(value) is None, (x, y)
        assert screen.selected_process is None and not screen.tags, (x, y)
        assert screen._history_selection == ('5:0', '8:0'), (x, y)
        screen.handle(ord('k'), value, sampler)
        assert screen.confirm is None, (x, y)
        # A new sample must not automatically restore a cancelled selection.
        value['sampled_at'] += 1
        screen.lines(value, 180, 60)
        assert screen.chosen_process(value) is None


def test_cancellation_does_not_consume_wheel_release_modal_or_subpage_clicks():
    value = frame()
    screen = Dashboard(options())
    sampler = Mock()
    screen.handle(curses.KEY_HOME, value, sampler)
    screen.lines(value, 180, 60)
    screen.mouse(3, 0, curses.BUTTON1_RELEASED, value, sampler)
    assert screen.chosen_process(value) is not None
    screen.mouse(3, 0, curses.BUTTON4_PRESSED, value, sampler)
    assert screen.chosen_process(value) is not None
    for modal, state in [('help', True), ('confirm', {'pid': 77}), ('result_modal', True)]:
        setattr(screen, modal, state)
        selected = screen.chosen_process(value)
        screen.mouse(3, 0, curses.BUTTON1_PRESSED, value, sampler)
        assert screen.chosen_process(value) == selected
        setattr(screen, modal, None if modal == 'confirm' else False)
    screen.open_view('metrics', value)
    screen.lines(value, 180, 60)
    selected = screen.chosen_process(value)
    screen.mouse(3, 0, curses.BUTTON1_PRESSED, value, sampler)
    assert screen.view == 'metrics' and screen.chosen_process(value) == selected
