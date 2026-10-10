# SPDX-License-Identifier: GPL-3.0-only
"""Keyboard/mouse controller shared by snapshot-based screens."""

from __future__ import annotations

import curses
import signal
import time

import psutil

from nputop.api.inspection import identity
from nputop.api.monitor import host_pid_namespace
from nputop.gui.library.widestring import WideString


class Actions:
    def feed(self, key, frame, sampler):
        """Resolve a standalone Escape without losing Alt sequences."""
        if self.escape_at is not None:
            if key == -1 and time.monotonic() - self.escape_at < 0.025:
                return True
            self.escape_at = None
            alt = {
                'j': curses.KEY_DOWN,
                'k': curses.KEY_UP,
                'h': curses.KEY_LEFT,
                'l': curses.KEY_RIGHT,
                'J': curses.KEY_NPAGE,
                'K': curses.KEY_PPAGE,
            }
            if key in [ord(c) for c in alt]:
                return self.handle(alt[chr(key)], frame, sampler)
            if not self.handle(27, frame, sampler):
                return False
        if key == 27:
            self.escape_at = time.monotonic()
            return True
        return self.handle(key, frame, sampler)

    def mouse(self, x, y, buttons, frame, sampler):
        if self.help or getattr(self, 'result_modal', False):
            return True
        if self.confirm:
            up, down = getattr(curses, 'BUTTON4_PRESSED', 0), getattr(curses, 'BUTTON5_PRESSED', 0)
            if buttons & (up | down):
                return self.handle(
                    curses.KEY_UP if buttons & up else curses.KEY_DOWN, frame, sampler
                )
            choice = next(
                (action for left, top, width, height, action in self.dialog_buttons
                 if left <= x < left + width and top <= y < top + height),
                None,
            )
            if buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                self.dialog_pressed = None
                if choice is not None:
                    return self.handle(ord('y') if choice else ord('n'), frame, sampler)
            elif buttons & curses.BUTTON1_PRESSED:
                self.dialog_pressed = choice
                if choice is not None:
                    self.confirm_choice = choice
            elif buttons & curses.BUTTON1_RELEASED:
                pressed, self.dialog_pressed = self.dialog_pressed, None
                if choice is not None and pressed == choice:
                    return self.handle(ord('y') if choice else ord('n'), frame, sampler)
            return True
        line = self.rendered[y] if 0 <= y < len(self.rendered) else None
        target = line.target if line and 0 <= x < len(WideString(line.text)) else None
        if target and self.view == 'main' and x in (0, len(WideString(line.text)) - 1):
            target = None  # A table border is not a process or device row.
        up, down = getattr(curses, 'BUTTON4_PRESSED', 0), getattr(curses, 'BUTTON5_PRESSED', 0)
        if buttons & (up | down):
            if buttons & getattr(curses, 'BUTTON_SHIFT', 0):
                key = curses.KEY_LEFT if buttons & up else curses.KEY_RIGHT
            else:
                if target and self.view == 'main':
                    self.focus = target[0]
                key = curses.KEY_UP if buttons & up else curses.KEY_DOWN
            return self.handle(key, frame, sampler)
        click = curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED
        if buttons & click and self.view == 'main' and (not target or target[0] != 'processes'):
            self.clear_selection()
        if target and buttons & click:
            self.selection_active = True
            pane, index = target
            if pane == 'tree':
                self.tree_index = index
                self.tree_selected = None
            elif pane == 'devices':
                self.focus, self.device_index = pane, index
            else:
                self.focus, self.process_index, self.selected_process = pane, index, None
            if buttons & curses.BUTTON1_DOUBLE_CLICKED:
                if pane == 'devices':
                    self.expanded = True
                else:
                    self.open_view('metrics', frame)
        return True

    def clear_selection(self):
        self.selection_active = False
        self.selected_process = None
        self.tags.clear()
        self.notice = ''

    def send_confirmed(self, confirmation, frame, sampler):
        # Keep signal checking in one boundary, including batch and tree actions.
        action = confirmation.get('_signal', 'SIGTERM')
        results = []
        for target in confirmation.get('_targets', [confirmation]):
            try:
                if getattr(self.args, 'readonly', False):
                    raise RuntimeError('Read-only mode: signals are disabled')
                fresh = frame is not None and time.monotonic() - frame['sampled_at'] <= max(
                    2, 2 * self.args.interval
                )
                if not fresh or sampler.error or target.get('created') is None:
                    raise RuntimeError('process snapshot is stale or identity is unavailable')
                namespace = host_pid_namespace()
                if (
                    not namespace
                    or target.get('pid_namespace') != namespace
                    or target.get('signal_allowed') is not True
                ):
                    raise RuntimeError('PID namespace is not verified; process is read-only')
                candidates = self.processes(frame)
                if (
                    self.inspection
                    and self.inspection.get('state') == 'ok'
                    and time.monotonic() - self.inspection.get('sampled_at', 0)
                    <= max(2, 2 * self.args.interval)
                ):
                    candidates = candidates + self.inspection.get('nodes', [])
                    if self.inspection.get('target'):
                        candidates = candidates + [self.inspection['target']]
                if not any(
                    identity(p) == identity(target) and p.get('signal_allowed') is True
                    for p in candidates
                ):
                    raise RuntimeError('process exited or current identity is unavailable')
                process = psutil.Process(target['pid'])
                if (
                    abs(process.create_time() - target['created']) > 0.001
                    or not process.is_running()
                ):
                    raise RuntimeError('PID was reused or process exited')
                process.send_signal(getattr(signal, action))
                results.append(f"{action} sent to PID {target['pid']}")
            except (psutil.Error, RuntimeError, OSError) as exc:
                results.append(f"PID {target['pid']}: {exc}")
        self.signal_results = results
        self.notice = (
            '; '.join(results)
            if len(results) <= 2
            else f'{len(results)} process results; Enter/Esc closes this report'
        )
        if len(results) > 2:
            self.result_modal = True
            self.modal_offset = 0
        self.tags.clear()

    def handle(self, key, frame, sampler):
        if key in (-1, curses.KEY_RESIZE):
            if key == curses.KEY_RESIZE and self.confirm:
                self.dialog_buttons = []
                self.dialog_pressed = None
                self.confirm_visible = False
            return True
        if self.confirm or getattr(self, 'result_modal', False):
            if key in (
                curses.KEY_UP,
                curses.KEY_DOWN,
                curses.KEY_PPAGE,
                curses.KEY_NPAGE,
                curses.KEY_HOME,
                curses.KEY_END,
            ):
                delta = {
                    curses.KEY_UP: -1,
                    curses.KEY_DOWN: 1,
                    curses.KEY_PPAGE: -(self.modal_page_size if self.confirm else self.page_size),
                    curses.KEY_NPAGE: self.modal_page_size if self.confirm else self.page_size,
                    curses.KEY_HOME: -1000000,
                    curses.KEY_END: 1000000,
                }[key]
                self.modal_offset = max(0, min(self.modal_limit, self.modal_offset + delta))
                return True
        if getattr(self, 'result_modal', False):
            if key in (10, 13, 27, ord('q')):
                self.result_modal = False
                self.notice = ''
            return True
        if self.help:
            if key in (ord('h'), ord('?'), 27, ord('q'), ord('Q')):
                self.help = False
            return True
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, buttons = curses.getmouse()
                return self.mouse(x, y, buttons, frame, sampler)
            except curses.error:
                return True
        if self.confirm is not None:
            if key in (9, curses.KEY_BTAB, curses.KEY_LEFT, curses.KEY_RIGHT):
                self.confirm_choice = not self.confirm_choice
                return True
            if key in (10, 13, curses.KEY_ENTER):
                key = ord('y') if self.confirm_choice else ord('n')
            if key in (ord('y'), ord('Y')):
                if self.confirm_visible:
                    target, self.confirm = self.confirm, None
                    self.send_confirmed(target, frame, sampler)
            elif key in (27, ord('n'), ord('N'), ord('q'), 3):
                self.confirm = None
            return True
        if self.sort_prefix:
            self.sort_prefix = False
            orders = {
                'n': 'natural',
                'u': 'user',
                'p': 'pid',
                'g': 'memory',
                's': 'npu_utilization',
                'c': 'cpu',
                'm': 'host_memory',
                't': 'time',
            }
            char = chr(key) if 0 <= key < 256 else ''
            if char.lower() in orders:
                if char.lower() == 's':
                    self.notice = 'Per-process NPU utilization is unavailable; sort unchanged.'
                else:
                    self.sort = orders[char.lower()]
                    self.sort_reverse = (self.sort not in ('natural', 'user')) != char.isupper()
                return True
        if key in (ord('h'), ord('?')):
            self.help = True
        elif key in (ord('q'), ord('Q'), 27):
            if self.view != 'main':
                self.view, self.inspection = 'main', None
                self.view_generation += 1
                self.notice = ''
            elif key == 27:
                self.clear_selection()
            else:
                return False
        elif key in (ord('t'), ord('e'), 10, 13, curses.KEY_ENTER):
            self.open_view(
                'tree' if key == ord('t') else 'environ' if key == ord('e') else 'metrics', frame
            )
        elif key == ord(' '):
            target = self.chosen_process(frame)
            if target and target.get('created') is not None and target.get('pid_namespace'):
                token = identity(target)
                if token in self.tags:
                    del self.tags[token]
                else:
                    self.tags[token] = dict(target)
                self.handle(curses.KEY_DOWN, frame, sampler)
        elif key in (ord('k'), ord('K'), ord('T'), ord('I'), 3):
            if getattr(self.args, 'readonly', False):
                self.notice = 'Read-only mode: signals are disabled'
            else:
                chosen = self.chosen_process(frame)
                targets = list(self.tags.values()) if self.tags else [chosen] if chosen else []
                if targets and all(p.get('signal_allowed') is True for p in targets):
                    targets = list({identity(p): dict(p) for p in targets}.values())
                    self.modal_offset = 0
                    self.confirm_choice = True
                    self.confirm_visible = True
                    self.dialog_buttons = []
                    self.dialog_pressed = None
                    self.confirm = dict(
                        targets[0],
                        _targets=targets,
                        _signal=(
                            'SIGTERM'
                            if key == ord('T')
                            else 'SIGINT'
                            if key in (3, ord('I'))
                            else 'SIGKILL'
                        ),
                    )
                elif targets:
                    self.notice = (
                        'Cannot signal: process identity/namespace is unverified or inaccessible.'
                    )
        elif key in (ord('r'), ord('R'), 18, curses.KEY_F5):
            if self.view == 'main':
                sampler.refresh()
                self.notice = 'Retrying the requested backend...'
            else:
                self.view_generation += 1
                self.inspection = None
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord('^'), ord('$'), 1, 5):
            attr, limit = (
                ('command_offset', self.command_limit)
                if self.view == 'main'
                else ('view_horizontal', self.view_width)
            )
            value = (
                0
                if key in (ord('^'), 1)
                else (
                    max(0, limit)
                    if key in (ord('$'), 5)
                    else max(0, getattr(self, attr) + (8 if key == curses.KEY_RIGHT else -8))
                )
            )
            setattr(self, attr, value)
        elif key in (9, curses.KEY_BTAB):
            self.focus = 'processes'
            return self.handle(curses.KEY_DOWN if key == 9 else curses.KEY_UP, frame, sampler)
        elif key in (ord('['), ord(']'), curses.KEY_PPAGE, curses.KEY_NPAGE):
            delta = -self.page_size if key in (ord('['), curses.KEY_PPAGE) else self.page_size
            if self.view == 'main':
                self.screen_offset = max(0, self.screen_offset + delta)
            else:
                self.view_offset = max(0, min(self.view_limit, self.view_offset + delta))
                if self.view == 'tree':
                    self.tree_index = max(0, min(len(self.tree_rows) - 1, self.tree_index + delta))
                    self.tree_selected = None
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END):
            delta = {
                curses.KEY_UP: -1,
                curses.KEY_DOWN: 1,
                curses.KEY_HOME: -1000000,
                curses.KEY_END: 1000000,
            }[key]
            was_selected = self.selection_active
            self.selection_active = True
            self.follow_selection = True
            if self.view == 'tree':
                self.tree_index = max(0, min(len(self.tree_rows) - 1, self.tree_index + delta))
                self.tree_selected = None
            elif self.view != 'main':
                self.view_offset = max(0, min(self.view_limit, self.view_offset + delta))
            elif self.focus == 'devices':
                self.device_index = max(
                    0, min(len(self.devices(frame)) - 1, self.device_index + delta)
                )
            else:
                count = len(self.processes(frame))
                self.selection_active = count > 0
                self.selected_process = None
                if not was_selected:
                    # Match nvitop: first down/up starts at the first/last row.
                    self.process_index = (
                        max(0, count - 1) if key in (curses.KEY_UP, curses.KEY_END) else 0
                    )
                else:
                    self.process_index = max(0, min(count - 1, self.process_index + delta))
        elif self.view == 'main':
            if key == ord('d'):
                self.expanded = not self.expanded
            elif key in (ord('a'), ord('f'), ord('c')):
                self.display_mode = {ord('a'): 'auto', ord('f'): 'full', ord('c'): 'compact'}[key]
            elif key == ord('o'):
                self.sort_prefix = True
            elif key in (ord('s'), ord('.'), ord(','), ord('<'), ord('>')):
                orders = ('memory', 'cpu', 'host_memory', 'time', 'pid', 'user', 'natural')
                self.sort = orders[
                    (orders.index(self.sort) + (-1 if key in (ord(','), ord('<')) else 1))
                    % len(orders)
                ]
                self.sort_reverse = self.sort not in ('natural', 'user')
            elif key == ord('/'):
                self.sort_reverse = not self.sort_reverse
        return True
