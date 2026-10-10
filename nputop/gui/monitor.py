# SPDX-License-Identifier: GPL-3.0-only
"""Ascend-oriented terminal dashboard. Rendering never queries hardware."""

from __future__ import annotations

import curses
import getpass
import os
import time
import sys
from collections import defaultdict, deque
from dataclasses import dataclass

from nputop.api.monitor import clean, numeric, healthy
from nputop.api.inspection import Inspector, identity
from nputop.gui.actions import Actions
from nputop.gui.process_views import ProcessViews
from nputop.gui.library.widestring import WideString
from nputop.version import __version__
from nputop.api.visibility import selection_error


@dataclass
class Line:
    text: str
    style: str = 'normal'
    target: tuple | None = None
    spans: tuple = ()  # (column, width, style[, shared emphasis]), excluding borders
    emphasis: str = ''  # selection brightness is independent of metric colors
    shared_power: tuple = ()  # (column, width, emphasis), independent of one die's selection


def draw_line(window, y, line, width, styles, x=0):
    """Keep selection brightness on colored fields without reversing device bars."""

    def attributes(style):
        value = styles.get(style, 0)
        if line.emphasis:
            value &= ~(curses.A_BOLD | curses.A_DIM)
            value |= styles.get(line.emphasis, 0)
        return value

    text = str(
        WideString(''.join(c if c.isprintable() else ' ' for c in line.text))[: max(0, width - x - 1)]
    )
    window.addstr(y, x, text, attributes(line.style))
    for span in line.spans:
        start, length, style = span[:3]
        part = str(WideString(text)[start : start + length])
        if part:
            attr = attributes(style)
            if len(span) > 3:
                attr = styles.get(style, 0)
                if span[3]:
                    attr = (attr & ~(curses.A_BOLD | curses.A_DIM)) | styles.get(span[3], 0)
            window.addstr(y, x + start, part, attr)
    if line.shared_power:
        start, length, emphasis = line.shared_power
        part = str(WideString(text)[start : start + length])
        attr = styles.get(line.style, 0)
        if emphasis:
            attr = (attr & ~(curses.A_BOLD | curses.A_DIM)) | styles.get(emphasis, 0)
        if part:
            window.addstr(y, x + start, part, attr)
    column = x
    for char in text:
        if char in '│|':
            window.addstr(y, column, char, styles['border'])
        column += len(WideString(char))


def ascii_text(text):
    return (
        text.translate(str.maketrans({'·': '-', '→': '>', '↳': '>', '—': '-', '─': '-'}))
        .encode('ascii', 'replace')
        .decode('ascii')
    )


def size(value):
    if not numeric(value):
        return '--'
    return f'{value / 1024**3:.2f} GiB'


def elapsed(value):
    if not numeric(value):
        return '--'
    seconds = max(0, int(value))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{days}d {hours:02}:{minutes:02}' if days else f'{hours}:{minutes:02}:{seconds:02}'


def load_style(value, memory=False):
    if not numeric(value):
        return 'muted'
    return 'high' if value >= (80 if memory else 75) else 'warning' if value >= 10 else 'device'


def metric_text(metric, unit='', digits=0):
    if not metric or metric['value'] is None:
        return '--'
    value = metric['value']
    suffix = '*' if metric['state'] == 'stale' else ''
    return f'{value:.{digits}f}{unit}{suffix}'


def displayed_utilization(row):
    """Keep raw overall telemetry intact; only 310P has an explicit display fallback."""
    metrics = row['metrics']
    overall = metrics.get('npu_overall', {'value': None, 'state': 'unsupported'})
    if '310P' in row['model'].upper() and overall['state'] == 'unsupported':
        return metrics.get('aicore', {'value': None, 'state': 'unsupported'}), 'AICore fallback'
    return overall, 'NPU overall'


def power_reference_text(reference):
    watts = reference.get('value_w')
    return f'{watts:g}W' if numeric(watts) else 'NA'


def displayed_power(row, inventory):
    """Group by enumerated card, never adjacency, display ID or a fixed die count.

    Use the freshest valid reading, then freshest stale reading, without
    summing or averaging. Grouping does not prove card-level measurement.
    References with card scope are displayed once, without per-die allocation.
    The full snapshot is used even when --only hides a member.
    """
    members = sorted((r for r in inventory if r['card'] == row['card']), key=lambda r: r['chip'])
    grouped = len(members) > 1
    reference = row.get('power_reference', {})
    references = [r.get('power_reference', {}) for r in members]
    if grouped:
        # Do not relabel per-chip model estimates as a whole-card reference.
        shared = all(r.get('scope') == 'card-shared' for r in references)
        watts = [r.get('value_w') for r in references]
        valid = [w for w in watts if numeric(w)]
        # Conflicting references cannot be resolved into a reliable card value.
        reference_value = (
            valid[0] if shared and valid and all(w == valid[0] for w in valid) else None
        )
    else:
        reference_value = reference.get('value_w')
    reference_text = power_reference_text({'value_w': reference_value})
    candidates = [
        r
        for r in members
        if numeric(r['metrics']['power'].get('value'))
        and r['metrics']['power']['state'] in ('ok', 'stale')
    ]
    # max() keeps the first (lowest chip ID) on timestamp ties. Selection does
    # not depend on inventory order, display filtering, or which die is clicked.
    chosen = max(
        candidates,
        key=lambda r: (
            r['metrics']['power']['state'] == 'ok',
            r['metrics']['power'].get('at') if numeric(r['metrics']['power'].get('at')) else -1,
        ),
        default=None,
    )
    dies = all(r.get('power_reference', {}).get('scope') == 'card-shared' for r in members)
    return {
        'members': members,
        'grouped': grouped,
        'label': f'{len(members)} '
        + (('die' if dies else 'chip') if len(members) == 1 else ('dies' if dies else 'chips')),
        'current': power_text(chosen['metrics']['power']) if chosen else 'NA',
        'current_source': chosen['key'] if chosen else None,
        'reference': reference_text,
        'reference_value': reference_value,
        'metric': chosen['metrics']['power'] if chosen else {'value': None, 'state': 'unavailable'},
    }


def power_text(metric):
    if not numeric(metric.get('value')):
        return 'NA'
    return metric_text(metric, 'W', 1)


def power_pair(group, width, prefix='', rounded=False):
    """nvitop-style current / reference, right aligned; never clip a stale marker."""
    current, reference = group['current'], group['reference']
    if rounded:
        metric = group['metric']
        current = metric_text(metric, 'W') if numeric(metric.get('value')) else 'NA'
        reference = (
            f"{group['reference_value']:.0f}W" if numeric(group['reference_value']) else 'NA'
        )
    text = prefix + current + ' / ' + reference
    if len(text) > width:
        text = prefix + current + '/' + reference
    return text.rjust(width)


def trend(values, width=16, ascii_only=False):
    chars = ' .:-=+*#' if ascii_only else ' ▁▂▃▄▅▆▇█'
    return ''.join(
        chars[min(len(chars) - 1, max(0, int(v / 100 * (len(chars) - 1))))] if numeric(v) else ' '
        for v in list(values)[-width:]
    ).rjust(width)


def bar(percent, width=12, ascii_only=False):
    if not numeric(percent):
        return ' ' * width
    filled = min(width, max(0, round(percent / 100 * width)))
    return ('#' if ascii_only else '█') * filled + ' ' * (width - filled)


def moving_bar(label, metric, width, ascii_only=False, tail='', value_text=None, keep_tail=False):
    """Keep the value beside the filled part; reserve a fixed trailing clock."""
    text = (
        value_text
        if value_text is not None
        else metric_text(metric, '%', 1 if label == 'MEM' else 0)
    )
    prefix = ' ' + label + ' '
    tail = (' ' + tail) if tail else ''
    if not keep_tail and width < len(prefix) + len(text) + len(tail) + 6:
        tail = ''  # Keep the value readable when a narrow column cannot fit a clock.
    room = max(0, width - len(prefix) - len(text) - len(tail) - 2)
    value = metric.get('value')
    filled = min(room, max(0, round(value / 100 * room))) if numeric(value) else 0
    body = ('#' if ascii_only else '█') * filled
    if not body and numeric(value) and room:
        body = '|' if ascii_only else '▏'
    return (prefix + body + ' ' + text).ljust(max(0, width - len(tail))) + tail


def power_bar(group, width, ascii_only=False):
    """Reference ratio, not a measured utilization or an enforced power limit."""
    metric, reference = group['metric'], group['reference_value']
    value = metric.get('value')
    ratio = (
        100 * value / reference if numeric(value) and numeric(reference) and reference > 0 else None
    )
    text = metric_text(metric, 'W') if numeric(value) else 'NA'
    fraction = dict(value=ratio, state=metric['state'])
    tail = metric_text(fraction, '%', 1) + ' ref' if numeric(ratio) else 'NA ref'
    return moving_bar('PWR', fraction, width, ascii_only, tail, text, keep_tail=True), ratio


def dot_history(values, width, height, mirrored=False, ascii_only=False):
    """Braille area chart; lower chart grows downwards from its top edge."""
    values = list(values)[-width:]
    output = []
    for row in range(height):
        chars = []
        for value in values:
            if not numeric(value):
                chars.append(' ')
                continue
            level = max(0, min(height * 4, round(value / 100 * height * 4)))
            mask = 0
            for dot in range(4):
                distance = row * 4 + dot if mirrored else (height - 1 - row) * 4 + 3 - dot
                if distance < level:
                    mask |= (0x09, 0x12, 0x24, 0xC0)[dot]
            chars.append(
                (':' if mask else ' ') if ascii_only else chr(0x2800 + mask) if mask else ' '
            )
        output.append(''.join(chars).rjust(width))
    return output


class Dashboard(Actions, ProcessViews):
    def __init__(self, args):
        self.args = args
        self.init_views()
        self.username = getpass.getuser()
        self.focus = 'processes'
        self.device_index = self.process_index = 0
        self.expanded = False
        self.display_mode = args.monitor
        self.command_offset = 0
        self.command_limit = 0
        self.help = False
        self.sort = 'memory'
        self.sort_reverse = True
        self.history = defaultdict(lambda: deque(maxlen=600))
        self.device_samples = deque(maxlen=600)
        self.last_frame = None
        self.backend = None
        self.notice = ''
        self.confirm = None
        self.confirm_choice = True
        self.confirm_visible = True
        self.dialog_lines = []
        self.dialog_position = (0, 0)
        self.dialog_buttons = []
        self.dialog_pressed = None
        self.modal_page_size = 1
        self.selected_process = None
        self.rendered = []

    def devices(self, frame):
        rows = frame['devices'] if frame else []
        if self.args.only is not None:
            rows = [row for row in rows if row['id'] in self.args.only]
        visible = getattr(self.args, 'visible_ids', None)
        if visible is not None and self.args.only is None:
            if not visible:
                return []
            if any(row.get('logical_id') is None for row in rows):
                self.notice = 'Visible-device mapping unavailable; no devices selected. Use --only with display IDs.'
                return []
            available = {row['logical_id'] for row in rows}
            effective = []
            for logical in visible:
                if logical not in available:
                    self.notice = (
                        f'Runtime ID {logical} unavailable; visibility ends before this ID.'
                    )
                    break
                effective.append(logical)
            rows = [row for row in rows if row['logical_id'] in effective]
        card_order = {}
        for row in rows:
            card_order.setdefault(row['card'], len(card_order))
        return sorted(rows, key=lambda row: (card_order[row['card']], row['chip']))

    def load_style(self, value, memory=False):
        thresholds = getattr(
            self.args, 'mem_util_thresh' if memory else 'npu_util_thresh', None
        ) or ((10, 80) if memory else (10, 75))
        return (
            'muted'
            if not numeric(value)
            else (
                'high'
                if value >= thresholds[1]
                else 'warning'
                if value >= thresholds[0]
                else 'device'
            )
        )

    def bar_style(self, value, memory=False):
        if getattr(self.args, 'colorful', False) and numeric(value):
            return 'load' + str(max(0, min(10, int(value / 10))))
        return self.load_style(value, memory)

    def processes(self, frame):
        devices = {row['key'] for row in self.devices(frame)}
        rows = [row for row in frame['processes'] if row['device'] in devices] if frame else []
        if self.args.user is not None:
            rows = [row for row in rows if row.get('user') in self.args.user]
        if self.args.pid is not None:
            rows = [row for row in rows if row['pid'] in self.args.pid]
        ranked = sorted(
            rows,
            key=lambda row: (
                (
                    row['pid']
                    if self.sort == 'natural'
                    else row.get(self.sort) or ('' if self.sort == 'user' else 0)
                ),
                row['pid'],
            ),
            reverse=self.sort_reverse,
        )
        order = {row['key']: i for i, row in enumerate(self.devices(frame))}
        if self.sort == 'natural':
            return sorted(
                rows, key=lambda row: (order[row['device']], row['pid']), reverse=self.sort_reverse
            )
        return sorted(ranked, key=lambda row: order[row['device']])

    def selected_key(self, frame):
        if getattr(self.args, 'visible_ids', None) is not None and (
            frame is None or frame['backend'] == 'smi'
        ):
            return '@mapping'
        rows = self.devices(frame)
        if not rows:
            return None
        if self.selection_active and self.focus == 'processes':
            procs = self.processes(frame)
            if procs:
                key = procs[min(self.process_index, len(procs) - 1)]['device']
                self.device_index = next(
                    (i for i, row in enumerate(rows) if row['key'] == key), self.device_index
                )
        self.device_index = min(self.device_index, len(rows) - 1)
        return rows[self.device_index]['key'] if self.expanded else None

    def accept(self, frame):
        if frame is None or frame['sampled_at'] == self.last_frame:
            return
        if self.backend != frame['backend']:
            self.history.clear()
            self.device_samples.clear()
            self.device_index = self.process_index = 0
        self.backend = frame['backend']
        self.last_frame = frame['sampled_at']
        for row in frame['devices']:
            value = row['metrics'][row.get('primary_utilization', 'aicore')]
            self.history[row['key']].append(value['value'] if value['state'] == 'ok' else None)

        host = frame['host']
        self.history['@time'].append(frame['sampled_at'])
        for key, field in (('@cpu', 'cpu'), ('@ram', 'memory_percent'), ('@swap', 'swap_percent')):
            self.history[key].append(host[field])
        sample = {}
        for row in frame['devices']:

            def valid_value(name):
                metric = row['metrics'].get(name, {})
                value = metric.get('value')
                return value if metric.get('state') == 'ok' and numeric(value) else None

            utilization, _ = displayed_utilization(row)
            sample[row['key']] = (
                valid_value('memory_used'),
                valid_value('memory_total'),
                utilization['value'] if utilization['state'] == 'ok' else None,
            )
        self.device_samples.append((frame['sampled_at'], sample))

    def lines(self, frame, width, height, error=None, *, paginate=True):
        self.accept(frame)
        self.page_size = max(1, height - 7)
        if paginate and getattr(self, '_terminal_size', None) != (width, height):
            self.follow_selection = self.selection_active
            self._terminal_size = (width, height)
            self.dialog_pressed = None
        if self.view != 'main' and not self.help:
            result = self.page_lines(frame, width, height)
        else:
            result = self._lines(frame, width, height, error, paginate=paginate)
            if paginate and not self.help:
                self.screen_limit = max(0, len(result) - height)
                viewport = max(1, height - (1 if self.screen_limit else 0))
                self.screen_limit = max(0, len(result) - viewport)
                self.screen_offset = min(self.screen_offset, self.screen_limit)
                if getattr(self, 'follow_selection', False) and self.selection_active:
                    index = self.device_index if self.focus == 'devices' else self.process_index
                    positions = [
                        i for i, line in enumerate(result) if line.target == (self.focus, index)
                    ]
                    if positions:
                        if positions[0] < self.screen_offset:
                            self.screen_offset = positions[0]
                        elif positions[-1] >= self.screen_offset + viewport:
                            self.screen_offset = positions[-1] - viewport + 1
                self.follow_selection = False
                count = len(result)
                result = result[self.screen_offset : self.screen_offset + viewport]
                if self.screen_limit:
                    result.append(
                        Line(
                            f' View {self.screen_offset + 1}-{min(count, self.screen_offset + viewport)}'
                            f' / {count} lines · PgUp/PgDn scroll',
                            'muted',
                        )
                    )
        if getattr(self, 'result_modal', False):
            self.modal_limit = max(0, len(self.signal_results) - height + 3)
            result = [
                Line(' Process signal results · Enter/Esc closes · arrows scroll', 'title')
            ] + [
                Line(' ' + text)
                for text in self.signal_results[
                    self.modal_offset : self.modal_offset + max(1, height - 3)
                ]
            ]
        elif self.notice and not self.help and not self.confirm:
            result = (
                result[: max(0, height - 2)] + [Line(' ' + self.notice, 'warning')]
                if paginate
                else result + [Line(' ' + self.notice, 'warning')]
            )
        if paginate:
            result = [
                Line(
                    str(WideString(line.text)[: max(0, width - 1)]),
                    line.style,
                    line.target,
                    line.spans,
                    line.emphasis,
                    line.shared_power,
                )
                for line in result[:height]
            ]
        if self.args.ascii:
            result = [
                Line(
                    ascii_text(line.text),
                    line.style,
                    line.target,
                    line.spans,
                    line.emphasis,
                    line.shared_power,
                )
                for line in result
            ]
        self.rendered = [] if self.help else result
        self.confirmation_dialog(width, height)
        return result

    def confirmation_dialog(self, width, height):
        """Keep confirmation above the live viewport, never replace its contents."""
        self.dialog_lines, self.dialog_buttons = [], []
        if not self.confirm or self.help or getattr(self, 'result_modal', False):
            return
        available = max(0, width - 1)
        box_width = min(64, available - 4 if available >= 40 else available)
        max_height = min(15, max(0, height - 2))
        self.confirm_visible = box_width >= 36 and max_height >= 10
        if not self.confirm_visible:
            # Do not send a signal when the target and action cannot be shown.
            self.dialog_lines = [
                Line(str(WideString(text)[:available]), 'warning')
                for text in ('Resize to confirm', 'Esc/n cancels')[: max(0, height)]
            ]
            self.dialog_position = (0, max(0, (height - len(self.dialog_lines)) // 2))
            return

        ascii_only = self.args.ascii
        vertical, horizontal = ('|', '-') if ascii_only else ('│', '─')
        top = '+' + '-' * (box_width - 2) + '+' if ascii_only else '╒' + '═' * (box_width - 2) + '╕'
        bottom = '+' + '-' * (box_width - 2) + '+' if ascii_only else '╘' + '═' * (box_width - 2) + '╛'

        def body(text='', style='normal'):
            text = ''.join(c if c.isprintable() else ' ' for c in str(text))
            if ascii_only:
                text = ascii_text(text)
            text = WideString(text)[: box_width - 4]
            padding = ' ' * (box_width - 4 - len(text))
            return Line(vertical + ' ' + str(text) + padding + ' ' + vertical, style)

        targets = self.confirm.get('_targets', [self.confirm])
        self.modal_page_size = min(6, max_height - 9)
        self.modal_limit = max(0, len(targets) - self.modal_page_size)
        self.modal_offset = min(self.modal_offset, self.modal_limit)
        visible = targets[self.modal_offset : self.modal_offset + self.modal_page_size]
        lines = [
            Line(top, 'border'),
            body('Send ' + self.confirm.get('_signal', 'SIGTERM') + '?', 'warning'),
            body(),
        ]
        for target in visible:
            lines.append(body('PID {} · {} · NPU {}'.format(
                target['pid'],
                target.get('user', '?'),
                target.get('device', ','.join(target.get('devices', []))),
            )))
        status = f'{len(targets)} process' + ('es' if len(targets) != 1 else '') + ' selected'
        if self.modal_limit:
            status = (
                f'{self.modal_offset + 1}-{self.modal_offset + len(visible)}/{len(targets)}'
                ' targets · Up/Down/PgUp/PgDn'
            )
        lines += [body(status, 'muted'), body('y confirm · Esc/n cancel', 'muted')]
        # Two bounded buttons; Enter activates the focused one, as in nvitop.
        start = 2 + (box_width - 4 - 32) // 2
        corners = ('+', '+', '+', '+') if ascii_only else ('┌', '┐', '└', '┘')
        button_top = corners[0] + horizontal * 13 + corners[1]
        button_bottom = corners[2] + horizontal * 13 + corners[3]
        labels = [
            ('> ' if self.confirm_choice else '  ') + 'Confirm (y)',
            ('  ' if self.confirm_choice else '> ') + 'Cancel (n)',
        ]
        button_y = len(lines)
        lines.append(body(' ' * (start - 2) + button_top + '  ' + button_top))
        labels_line = body(
            ' ' * (start - 2)
            + '  '.join(vertical + label.ljust(13) + vertical for label in labels)
        )
        labels_line.spans = (
            (start + 1, 13, 'warning' if self.confirm_choice else 'muted'),
            (start + 18, 13, 'muted' if self.confirm_choice else 'linked'),
        )
        lines += [
            labels_line,
            body(' ' * (start - 2) + button_bottom + '  ' + button_bottom),
            Line(bottom, 'border'),
        ]
        x, y = (available - box_width) // 2, (height - len(lines)) // 2
        self.dialog_position = (x, y)
        self.dialog_lines = lines
        self.dialog_buttons = [
            (x + start, y + button_y, 15, 3, True),
            (x + start + 17, y + button_y, 15, 3, False),
        ]

    def report(self, frame, width=115, error=None):
        """A complete text snapshot, with no terminal pagination or scrolling."""
        return self.lines(frame, width, 40, error, paginate=False)

    def _lines(self, frame, width, height, error=None, *, paginate=True):
        self.accept(frame)
        self.rendered = []
        if frame is None:
            return [
                Line(f' nputop {__version__}', 'title'),
                Line(''),
                Line(' ' + clean(error), 'warning') if error else Line(''),
                Line(' r retry · q quit', 'muted') if error else Line(''),
            ]
        rows = self.devices(frame)
        procs = self.processes(frame)
        if self.selected_process is not None:
            matches = [
                i
                for i, p in enumerate(procs)
                if (p['device'], p['pid'], p.get('created')) == self.selected_process
            ]
            if matches:
                self.process_index = matches[0]
            else:
                self.selection_active = False
                self.selected_process = None
        linked = set()
        if self.selection_active and self.focus == 'processes' and procs:
            chosen = procs[min(self.process_index, len(procs) - 1)]
            self.selected_process = (chosen['device'], chosen['pid'], chosen.get('created'))
            linked = {p['device'] for p in procs if identity(p) == identity(chosen)}
            self.device_index = next(
                (i for i, r in enumerate(rows) if r['key'] == chosen['device']), self.device_index
            )
        age = max(0, time.monotonic() - frame['sampled_at'])
        stale = age > max(2.0, 2 * self.args.interval) or error is not None
        status = (
            'STALE'
            if stale
            else ('LIVE' if healthy(frame) and frame['process_status'] == 'ok' else 'DEGRADED')
        )
        # Keep the original nputop device / host-history / process composition.
        # Layout consumes only snapshots; terminal resizing never queries the driver.
        # As in nvitop's device panel, keep a minimum logical canvas width.
        # Shrinking the viewport must not produce negative columns or truncate
        # borders before merged cells are drawn. lines()/draw_line() clip the
        # finished canvas to the real terminal; height still controls compaction.
        width = max(80, width)
        inner = width - 3
        edge = '|' if self.args.ascii else '│'
        horizontal = '-' if self.args.ascii else '─'

        def fit(text, length):
            if self.args.ascii:
                text = ascii_text(text)
            return str(WideString(text)[:length].ljust(length))

        def content(
            text, style='normal', target=None, spans=(), length=None, emphasis='', shared_power=()
        ):
            return Line(
                edge + fit(text, inner if length is None else length) + edge,
                style,
                target,
                spans,
                emphasis,
                shared_power,
            )

        def rule(
            label='',
            bottom=False,
            top=False,
            double=False,
            above=(),
            below=(),
            length=None,
            extend_right=False,
        ):
            length = inner if length is None else length
            stroke = '=' if self.args.ascii and double else horizontal
            if double and not self.args.ascii:
                stroke = '═'
            if self.args.ascii:
                left = right = '+'
            elif top:
                left, right = ('╒', '╕') if double else ('┌', '┐')
            elif bottom:
                left, right = ('╘', '╛') if double else ('└', '┘')
            else:
                left, right = ('╞', '╡') if double else ('├', '┤')
            if extend_right and not self.args.ascii:
                right = '╕' if double else '┐'
            body = list(stroke * length)
            for pos in set(above) | set(below):
                if 0 <= pos < length:
                    both = pos in above and pos in below
                    body[pos] = (
                        '+'
                        if self.args.ascii
                        else (
                            ('╪' if double else '┼')
                            if both
                            else (
                                ('╧' if double else '┴')
                                if pos in above
                                else ('╤' if double else '┬')
                            )
                        )
                    )
            if label:
                title = (' ' + label + ' ')[:length]
                body[: len(title)] = title
            return Line(left + ''.join(body) + right, 'border')

        def table(cells, widths):
            return edge.join(fit(cell, length) for cell, length in zip(cells, widths))

        def dividers(widths):
            return [sum(widths[:i]) + i - 1 for i in range(1, len(widths))]

        wide = width >= 100
        header_width = 78 if wide else inner
        clock = time.strftime('%a %b %d %H:%M:%S %Y')
        hint = '(Press h for help or q to quit)'
        result = [
            Line(' ' + clock + hint.rjust(max(len(hint) + 2, header_width - len(clock))), 'normal')
        ]
        result.append(rule(top=True, double=True, length=header_width))
        result.append(
            content(
                f" nputop {__version__}   Driver {frame.get('driver') or '--'}   CANN {frame.get('cann') or '--'}   {frame['backend'].upper()}"
                + (f'  {status}' if status != 'LIVE' else '')
                + ('  READONLY' if getattr(self.args, 'readonly', False) else ''),
                'title',
                length=header_width,
            )
        )
        if error or frame.get('reason'):
            result.append(content(' ' + clean(error or frame['reason']), 'warning'))
        overall = bool(rows) and all(r.get('primary_utilization') == 'npu_overall' for r in rows)
        utilization_label = 'NPU%' if overall else 'AICore'
        result.append(
            rule(
                f'Devices · {len(rows)} chips / {len({r["card"] for r in rows})} cards',
                length=header_width,
            )
        )
        # Like nvitop, compact devices before host charts. All devices remain in
        # one scrollable canvas; space is never taken from the inventory for a
        # fixed process/graph reservation. An explicit full mode is restored when
        # the terminal grows again, but cannot force premature device pagination.
        reserve = 22 + (10 if self.expanded else 0)
        full_fits = height >= len(result) + 3 + 3 * len(rows) + reserve
        full_requested = self.display_mode in ('auto', 'full')
        row_height = 3 if full_requested and (full_fits or not paginate) else 2
        if paginate and height < len(result) + 2 + 2 * len(rows) + 15:
            row_height = 1
        history_full = height >= len(result) + 2 + row_height * len(rows) + reserve
        self.row_height = row_height
        full = row_height == 3
        # Three large information blocks, as in nvitop: identity/sensors,
        # memory, utilization. Never partition ID, temperature and power.
        widths = [33, 26, header_width - 61]
        header_rows = (
            [
                [' ID  Name              Card:Chip', ' Bus-Id', ' Topology'],
                [
                    ' Temp             Pwr:Usage/Ref',
                    ' Memory used / total',
                    f' {utilization_label}',
                ],
            ]
            if full
            else [
                [
                    ' ID Card:Chip Temp  Pwr:Usage/Ref',
                    ' Memory used / total',
                    f' {utilization_label} Name',
                ],
            ]
        )
        extra = 0
        if wide:
            remaining = inner - header_width - 1
            extra = 2 if width >= (140 if full else 124) else 1
            if extra == 2:
                widths += [(remaining - 1) // 2, remaining // 2]
            else:
                widths += [remaining]
        separators = dividers(widths)
        for headers in header_rows:
            result.append(content(table(headers, widths), 'section', length=header_width))
        result.append(rule(double=True, above=separators[:3], below=separators, extend_right=wide))
        self.device_index = min(self.device_index, max(0, len(rows) - 1))
        power_groups = {}
        for row in rows:
            if row['card'] not in power_groups:
                power_groups[row['card']] = displayed_power(row, frame['devices'])
        visible_groups = defaultdict(list)
        for index, row in enumerate(rows):
            visible_groups[row['card']].append(index)
        multi_card_rows = any(g['grouped'] for g in power_groups.values())
        for index, row in enumerate(rows):
            m = row['metrics']
            util = m[row.get('primary_utilization', 'aicore')]
            overall_util, utilization_source = displayed_utilization(row)
            used, total = m['memory_used']['value'], m['memory_total']['value']
            pct = used / total * 100 if numeric(used) and numeric(total) and total > 0 else None
            memory = f'{size(used)} / {size(total)}'.replace(' GiB', 'GiB')
            if any(m[k]['state'] == 'stale' for k in ('memory_used', 'memory_total')):
                memory += '*'
            marker = (
                '>'
                if self.selection_active and self.focus == 'devices' and index == self.device_index
                else ' '
            )
            group = power_groups[row['card']]
            visible = visible_groups[row['card']]
            first = index == visible[0]
            filtered = len(visible) != len(group['members'])
            topology = (f'{len(visible)}/' if filtered else '') + group['label']
            if (
                not group['grouped']
                and row.get('power_reference', {}).get('scope') == 'card-shared'
            ):
                topology = '1 chip; card'
            shared_label = ('> ' if self.args.ascii else '↳ ') + f"Card {row['card']}"
            temperature = metric_text(m['temperature'], 'C')
            prefix = (
                f' {temperature:>4}'
                if full
                else f"{marker}{row['id']:>2} {row['key']:>9} {temperature:>4}"
            )
            power_width = widths[0] - len(prefix) - 2
            power = (
                power_pair(
                    group,
                    power_width,
                    f"{len(group['members'])}c " if filtered else '',
                    rounded=True,
                )
                if first
                else shared_label.rjust(power_width)
            )
            cells = [prefix + ' ' + power + ' ', f' {memory:>24}', f" {metric_text(util, '%'):>6}"]
            name = row['model']
            identity_cells = [
                f"{marker}{row['id']:>3}  " + fit(name, 16) + f" {row['key']:>9}",
                ' ' + (row.get('bus') or '--'),
                ' ' + topology if first else '',
            ]
            if not full:
                # Keep SKU suffixes without repeating an Ascend prefix in the
                # compact utilization/name block.
                cells[2] += ' ' + name.replace('Ascend', '').strip()
            style = max(
                (self.load_style(pct, True), self.load_style(util['value'])),
                key=lambda s: {'muted': 0, 'device': 1, 'warning': 2, 'high': 3}[s],
            )
            if stale or any(v['state'] == 'stale' for v in m.values()):
                style = 'warning'
            emphasis = ('linked' if row['key'] in linked else 'muted') if linked else ''
            power_emphasis = (
                ('linked' if any(r['key'] in linked for r in group['members']) else 'muted')
                if linked
                else ''
            )
            shared_power = (
                (len(prefix) + 2, power_width, power_emphasis) if group['grouped'] else ()
            )
            identity_spans, spans = [], []
            if wide:
                mem_metric = dict(
                    value=pct,
                    state='stale'
                    if any(m[k]['state'] == 'stale' for k in ('memory_used', 'memory_total'))
                    else m['memory_used']['state'],
                )
                mem_bar = moving_bar('MEM', mem_metric, widths[3], self.args.ascii)
                util_bar = moving_bar(
                    'UTL*' if utilization_source == 'AICore fallback' else 'UTL',
                    overall_util,
                    widths[3 if full else 4] if full or extra == 2 else widths[3],
                    self.args.ascii,
                    '@ ' + metric_text(m.get('aicore_mhz'), 'MHz'),
                )
                offset = header_width + 2
                if full:
                    identity_cells.append(mem_bar)
                    cells.append(util_bar)
                    identity_spans.append((offset, widths[3], self.bar_style(pct, True)))
                    spans.append((offset, widths[3], self.bar_style(overall_util['value'])))
                    if extra == 2:
                        bw = m.get('memory_bandwidth', dict(value=None, state='unsupported'))
                        freq = m.get('memory_mhz')
                        identity_cells.append(
                            moving_bar(
                                'MBW',
                                bw,
                                widths[4],
                                self.args.ascii,
                                '@ ' + metric_text(freq, 'MHz')
                                if freq and numeric(freq.get('value'))
                                else '',
                                value_text='NA' if bw['value'] is None else None,
                            )
                        )
                        pwr, ratio = power_bar(group, widths[4], self.args.ascii)
                        cells.append(pwr if first else ' PWR ' + shared_label)
                        offset += widths[3] + 1
                        identity_spans.append((offset, widths[4], self.bar_style(bw['value'])))
                        spans.append((offset, widths[4], self.bar_style(ratio), power_emphasis))
                else:
                    cells.append(mem_bar)
                    spans.append((offset, widths[3], self.bar_style(pct, True)))
                    if extra == 2:
                        cells.append(util_bar)
                        spans.append(
                            (
                                offset + widths[3] + 1,
                                widths[4],
                                self.bar_style(overall_util['value']),
                            )
                        )
            if full:
                result.append(
                    content(
                        table(identity_cells, widths),
                        style,
                        ('devices', index),
                        tuple(identity_spans),
                        emphasis=emphasis,
                    )
                )
            result.append(
                content(
                    table(cells, widths),
                    style,
                    ('devices', index),
                    tuple(spans),
                    emphasis=emphasis,
                    shared_power=shared_power,
                )
            )
            if row_height >= 2:
                new_card = index + 1 < len(rows) and rows[index + 1]['card'] != row['card']
                result.append(
                    rule(double=multi_card_rows and new_card, above=separators, below=separators)
                )
        if not rows:
            result.append(content(' No devices match the selection.', 'warning'))
        host = frame['host']
        graph_rows = [r for r in rows if r['key'] in linked] if linked else rows
        valid = [
            r
            for r in graph_rows
            if all(
                r['metrics'][key]['state'] == 'ok' and numeric(r['metrics'][key]['value'])
                for key in ('memory_used', 'memory_total')
            )
            and r['metrics']['memory_total']['value'] > 0
        ]
        used = sum(r['metrics']['memory_used']['value'] for r in valid)
        total = sum(r['metrics']['memory_total']['value'] for r in valid)
        memory_pct = 100 * used / total if total else None
        values = [
            displayed_utilization(r)[0]['value']
            for r in graph_rows
            if displayed_utilization(r)[0]['state'] == 'ok'
        ]
        values = [v for v in values if numeric(v)]
        utilization = sum(values) / len(values) if values else None
        history_key = tuple(r['key'] for r in graph_rows)
        if getattr(self, '_device_sample', None) != (frame['sampled_at'], history_key):
            self._history_selection = history_key
            self._device_sample = (frame['sampled_at'], history_key)
            for key in ('@mem', '@util', '@device_time'):
                self.history[key].clear()
            for at, sample in self.device_samples:
                observations = [sample.get(key, (None, None, None)) for key in history_key]
                complete_memory = observations and all(
                    numeric(u) and numeric(t) and t > 0 for u, t, _ in observations
                )
                complete_util = observations and all(numeric(v) for _, _, v in observations)
                self.history['@device_time'].append(at)
                self.history['@mem'].append(
                    100 * sum(u for u, _, _ in observations) / sum(t for _, t, _ in observations)
                    if complete_memory
                    else None
                )
                self.history['@util'].append(
                    sum(v for _, _, v in observations) / len(observations)
                    if complete_util
                    else None
                )
        half = 78 if wide else (inner - 1) // 2
        result.append(
            rule(
                'Host / NPU history'
                + (f" · PID {chosen['pid']}" if linked else ' · displayed devices'),
                double=True,
                above=separators,
                below=[half],
            )
        )

        def pair(left, right, style='normal', right_style=None):
            spans = ((half + 2, inner - half - 1, right_style),) if right_style else ()
            result.append(content(fit(left, half) + edge + right, style, spans=spans))

        def percent(v):
            return f'{v:.1f}%' if numeric(v) else '--'

        load = ' '.join(f'{v:.2f}' for v in host.get('load', [])) or '--'
        pair(
            f' Load Average: {load}' if history_full else f" CPU {host['cpu']:.1f}%",
            f' NPU MEM {percent(memory_pct)} · {len(valid)}/{len(graph_rows)} chips',
        )
        if history_full:
            pair(f" CPU {host['cpu']:.1f}%", '', 'cpu')
        graph_height = 3 if history_full else 1

        def graphs(left, right, style, right_style, mirrored=False):
            def plot(key, length):
                # Timestamp buckets preserve gaps when sampling stalls.
                slots = [None] * length
                for at, value in zip(
                    self.history['@device_time' if key in ('@mem', '@util') else '@time'],
                    self.history[key],
                ):
                    age = max(0, frame['sampled_at'] - at)
                    index = length - 1 - round(age / self.args.interval)
                    if 0 <= index < length:
                        slots[index] = value
                return dot_history(slots, length, graph_height, mirrored, self.args.ascii)

            left_graph, right_graph = plot(left, half - 2), plot(right, inner - half - 3)
            for l, r in zip(left_graph, right_graph):
                pair(' ' + l, ' ' + r, style, right_style)

        def axis(length):
            chars = list(horizontal * length)
            occupied = set()
            for seconds in (0, 30, 60, 120, 180, 240, 300):
                pos = length - 1 - round(seconds / self.args.interval)
                label = f'{seconds}s'
                start = pos - len(label) + 1
                if start >= 0 and not any(i in occupied for i in range(start - 1, pos + 2)):
                    chars[start : pos + 1] = label
                    occupied.update(range(start - 1, pos + 2))
            return ''.join(chars)

        graphs('@cpu', '@mem', 'cpu', self.bar_style(memory_pct, True))
        pair(' ' + axis(half - 2), ' ' + axis(inner - half - 3), 'muted')
        graphs('@ram', '@util', 'chart', self.bar_style(utilization), mirrored=True)
        pair(
            f" MEM {size(host['memory_used'])} ({host['memory_percent']:.1f}%)",
            f' AVG NPU UTL {percent(utilization)} - {len(values)}/{len(graph_rows)} chips',
            'normal',
        )
        pair(
            f" SWP {size(host.get('swap_used'))} ({host['swap_percent']:.1f}%)",
            (
                ' UTL*: AICore fallback (310P)'
                if any(displayed_utilization(r)[1] == 'AICore fallback' for r in graph_rows)
                else ' UTL: NPU overall · newest at right'
            ),
            'muted',
        )
        if history_full:
            pair(
                ' '
                + dot_history(self.history['@swap'], half - 2, 1, ascii_only=self.args.ascii)[0],
                '',
                'swap',
            )
        result.append(rule(bottom=True, double=True, above=[half]))
        if self.expanded and rows and height >= 38:
            row = rows[self.device_index]
            result.append(
                content(
                    f" {frame['backend'].upper()} · sample {frame['collection_ms']:.0f} ms · age {age:.1f}s · interval {self.args.interval:g}s",
                    'muted',
                )
            )
            result.append(
                Line(
                    f" DETAILS  Card:Chip: {row['key']}   {row['model']}   PCI {row.get('bus') or '--'}",
                    'section',
                )
            )
            logical = row.get('logical_id')
            physical = row.get('chip_physical_id')
            result.append(
                Line(
                    f" Display ID: {row['id']}   Logical ID: {logical if logical is not None else '--'}"
                    f"   Physical ID: {physical if physical is not None else '--'}"
                    f"   Mapping: {row.get('id_source') or 'unavailable'}",
                    'muted',
                )
            )
            details = row['details']
            result.append(
                Line(
                    ' UTL display: '
                    + displayed_utilization(row)[1]
                    + '; Power source: '
                    + (row.get('power_source') or frame['backend'].upper()),
                    'muted',
                )
            )
            if not details:
                message = (
                    'Collecting selected device details...'
                    if frame['backend'] == 'dcmi'
                    else 'Extended metrics unavailable through SMI.'
                )
                result.append(Line(' ' + message, 'muted'))
            else:
                labels = [
                    ('npu_overall', 'NPU overall', '%'),
                    ('aicpu', 'AICPU', '%'),
                    ('memory_bandwidth', 'Memory BW', '%'),
                    ('memory_mhz', 'Memory clock', ' MHz'),
                    ('hbm_temperature', 'HBM temp', 'C'),
                    ('aicore_mhz', 'AICore clock', ' MHz'),
                    ('ecc_uncorrected', 'ECC uncorrected', ''),
                    ('pcie_tx', 'PCIe TX', ' GiB/s'),
                    ('pcie_rx', 'PCIe RX', ' GiB/s'),
                ]
                tokens = []
                for key, label, unit in labels:
                    metric = details.get(key)
                    if metric is None:
                        continue
                    value = metric_text(metric, unit, 2 if key.startswith('pcie') else 0)
                    if metric['value'] is None:
                        value = metric['state']
                    tokens.append(f'{label}: {value}')
                for i in range(0, len(tokens), 3):
                    result.append(Line(' ' + '   |   '.join(tokens[i : i + 3]), 'normal'))
            for name, title in (('memory_bandwidth', 'MBW'), ('memory_mhz', 'Memory clock')):
                metric = row['metrics'].get(name, {})
                source = metric.get('source') or 'unavailable through this backend'
                reason = metric.get('reason')
                result.append(
                    Line(f' {title} source: {source}' + (f'; {reason}' if reason else ''), 'muted')
                )
            result.append(
                Line(
                    ' Power unit: W; Ref is a rated/model reference, not a verified enforced power limit.',
                    'muted',
                )
            )
            group = displayed_power(row, frame['devices'])
            if group['grouped']:
                result.append(
                    Line(
                        f" Card {row['card']} · {group['label']}: {group['current']}; "
                        f"source {group['current_source'] or 'unavailable'}; newest valid reading, then newest stale reading.",
                        'muted',
                    )
                )
                for member in group['members']:
                    metric = member['metrics']['power']
                    result.append(
                        Line(
                            f"   {member['key']}: {power_text(metric)} ({metric['state']})"
                            + (f" · {metric['reason']}" if metric.get('reason') else ''),
                            'muted',
                        )
                    )
                result.append(
                    Line(
                        ' Grouped display is not a sum or average, and does not establish measurement scope.',
                        'muted',
                    )
                )
                result.append(
                    Line(
                        ' Card reference: '
                        + group['reference']
                        + '; shared references shown once, never allocated per die.',
                        'muted',
                    )
                )
            elif row.get('power_reference', {}).get('scope') == 'card-shared':
                result.append(
                    Line(' Only one chip visible; reference retains whole-card scope.', 'muted')
                )
            result.append(
                Line(
                    f" Raw power: {power_text(row['metrics']['power'])} ({row.get('power_scope', 'unknown')})",
                    'muted',
                )
            )
            reference = row.get('power_reference', {})
            reference_text = power_reference_text(reference)
            result.append(
                Line(
                    f" Raw power reference: {reference_text} ({reference.get('scope', 'unknown')})"
                    f"  {reference.get('source') or 'unavailable'}",
                    'muted',
                )
            )
            result.append(Line(' ' + reference.get('reason', 'No reference available'), 'muted'))
        if height >= 30:
            result.append(Line(''))
        process_start = len(result)
        procs = self.processes(frame)
        result.append(
            Line(
                f" Processes: {len(procs)} · tagged {len(self.tags)}  "
                + f'grouped by chip · sort: {self.sort} {"descending" if self.sort_reverse else "ascending"}'
                + (f"    PID {chosen['pid']} → {', '.join(sorted(linked))}" if linked else ''),
                'normal',
            )
        )
        if frame['process_status'] != 'ok':
            result.append(
                Line(
                    ' Process information '
                    + frame['process_status']
                    + ': '
                    + '; '.join(frame['errors']),
                    'warning',
                )
            )
        result.append(
            Line(
                f" {'CARD:CHIP':>9} {'PID':>8} {'USER':<10} {'NPU-MEM':>9} {'%CPU':>6} {'%MEM':>5} {'TIME':>10} COMMAND",
                'section',
            )
        )
        result.append(Line('__process_header__', 'border'))
        available = 2 * len(procs)
        self.process_index = min(self.process_index, max(0, len(procs) - 1))
        process_offset = 0
        used_rows = 0
        previous_device = None
        self.command_limit = max(
            (len(WideString(p.get('command', ''))) - max(1, inner - 65) for p in procs), default=0
        )
        self.command_offset = min(self.command_offset, max(0, self.command_limit))
        for index in range(process_offset, len(procs)):
            p = procs[index]
            separator = previous_device is not None and previous_device != p['device']
            if used_rows + 1 + separator > available:
                break
            if separator:
                result.append(Line('__process_separator__', 'border'))
                used_rows += 1
            previous_device = p['device']
            used_rows += 1
            cpu = f"{p['cpu']:.1f}" if numeric(p.get('cpu')) else '--'
            memory = f"{p['host_memory']:.1f}" if numeric(p.get('host_memory')) else '--'
            tag = '*' if identity(p) in self.tags else ' '
            prefix = f"{tag}{p['device']:>9} {p['pid']:>8} {fit(p.get('user', '?'), 10)} {size(p['memory']).replace(' ', ''):>9} {cpu:>6} {memory:>5} {elapsed(p.get('time')):>10} "
            # Keep identity columns fixed while exploring long command lines.
            command_text = p.get('command', '')
            command = WideString(ascii_text(command_text) if self.args.ascii else command_text)
            command_width = max(1, inner - len(WideString(prefix)))
            command_offset = min(self.command_offset, max(0, len(command) - command_width))
            text = prefix + str(command[command_offset:])
            result.append(
                Line(
                    text,
                    (
                        'selected'
                        if self.selection_active
                        and self.focus == 'processes'
                        and index == self.process_index
                        else (
                            'muted' if p.get('user') not in (None, '?', self.username) else 'normal'
                        )
                    ),
                    ('processes', index),
                )
            )
        if not procs and frame['process_status'] == 'ok':
            filtered = self.args.user is not None or self.args.pid is not None
            result.append(
                Line(
                    ' No matching processes.' if filtered else ' No running NPU processes.', 'muted'
                )
            )
        result[process_start:] = (
            [rule('Processes', top=True, double=True)]
            + [
                (
                    rule(double=line.text == '__process_header__')
                    if line.text in ('__process_separator__', '__process_header__')
                    else content(line.text, line.style, line.target)
                )
                for line in result[process_start:]
            ]
            + [rule(bottom=True, double=True)]
        )
        if self.help:
            return [Line(' nputop / keyboard help · Esc/h/q back', 'title')] + [
                Line(' ' + line)
                for line in (
                    'Mouse click/wheel: select; double click: details; Shift-wheel: horizontal scroll',
                    'Click outside process rows: clear process selection and tags',
                    'Up/Down or Alt-k/j, Tab/Shift-Tab: select; Home/End: first/last',
                    'PageUp/Down, [ / ], Alt-K/J: scroll page; Left/Right or Alt-h/l: horizontal',
                    'Ctrl-a/^ and Ctrl-e/$: horizontal start/end; Esc: clear selection and tags',
                    't: process tree; Enter: process metrics; e: environment; Esc/q: back',
                    'Space: tag/untag process; tagged processes receive batch actions',
                    'k/K: SIGKILL; T: SIGTERM; I/Ctrl-C: SIGINT; y confirms, Esc/n cancels',
                    'Confirmation dialog: Tab/Left/Right selects a button; Enter or click activates',
                    '--readonly disables all signals; PID identity is checked again before delivery',
                    's/./,: sort column; /: reverse; on/ou/op/og/oc/om/ot: direct sort',
                    'Uppercase second sort key reverses direction; os: unavailable process NPU utilization',
                    'a/f/c: auto/full/compact; d: Ascend device details; r/Ctrl-r/F5: refresh',
                    'q: quit main screen; no signal action when no process is selected',
                    'UTL: overall NPU (DCMI selector 13); AICore: selector 2, different metrics',
                    '310P UTL*: AICore fallback if overall is unsupported; raw overall stays missing',
                    'Missing metrics are not zero; * marks a previous valid sample',
                    'd details: Logical ID is driver/runtime ID; Physical ID is mapped chip ID',
                    'Display ID (--only) and Card:Chip are separate; missing ID mappings stay --',
                    'Power: current / reference (W); newest valid same-card reading, no sums or averages',
                    'Ref has no ~ prefix; rated/model source and unit/scope assumptions remain in d details',
                    'Environment is loaded only on request and excluded from diagnostic exports',
                    '--legacy-ui: previous interface; --diagnose: redacted capability report',
                )
            ]
        self.rendered = result
        return result


def wait_for_frame(sampler, dashboard):
    """Prepare the first snapshot before opening curses, with cancellable input.

    Polling remains nonblocking and the sampler owns driver deadlines/fallback.
    A real collection error opens the dashboard's retry/error page immediately.
    """
    import sys

    terminal, settings = None, None
    try:
        if os.name != 'nt' and sys.stdin.isatty():
            import select
            import termios
            import tty

            terminal = sys.stdin.fileno()
            settings = termios.tcgetattr(terminal)
            tty.setcbreak(terminal, termios.TCSANOW)
        while sampler.poll(dashboard.selected_key(sampler.frame)) is None and not sampler.error:
            key = b''
            if terminal is not None:
                if select.select([terminal], [], [], 0.025)[0]:
                    key = os.read(terminal, 1)
                    if not key:
                        return False
            elif os.name == 'nt':
                import msvcrt

                key = msvcrt.getch() if msvcrt.kbhit() else b''
                time.sleep(0.025)
            else:
                time.sleep(0.025)
            if key in (b'q', b'Q'):
                return False
            if key == b'\x03':
                raise KeyboardInterrupt
        return True
    finally:
        if settings is not None:
            termios.tcsetattr(terminal, termios.TCSANOW, settings)


def run_dashboard(sampler, args):
    # Python 3.7/3.8 do not expose set_escdelay; ncurses reads this at startup.
    os.environ.setdefault('ESCDELAY', '25')
    dashboard = Dashboard(args)
    if not wait_for_frame(sampler, dashboard):
        return
    error = selection_error(sampler.frame, args)
    if error:
        print('nputop: ' + error, file=sys.stderr)
        return 2
    inspector = Inspector()

    def loop(window):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        styles = {
            'title': 0,
            'section': 0,
            'selected': curses.A_REVERSE,
            'linked': curses.A_BOLD,
            'muted': curses.A_DIM,
            'warning': curses.A_BOLD,
            'normal': 0,
            'border': 0,
            'device': 0,
            'chart': 0,
            'cpu': 0,
            'high': curses.A_BOLD,
            'swap': 0,
        }
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_GREEN, -1)
                curses.init_pair(4, curses.COLOR_MAGENTA, -1)
                curses.init_pair(5, curses.COLOR_RED, -1)
                curses.init_pair(6, curses.COLOR_BLUE, -1)
                styles['swap'] |= curses.color_pair(6)
                if getattr(args, 'light', False) and curses.COLORS >= 256:
                    for pair, color in [(1, 24), (2, 130), (3, 28), (4, 90), (5, 124)]:
                        curses.init_pair(pair, color, -1)
                for level in range(11):
                    styles['load' + str(level)] = styles['muted']
                    if curses.COLORS >= 256:
                        color = (28, 34, 40, 76, 112, 148, 184, 220, 214, 208, 196)[level]
                        curses.init_pair(10 + level, color, -1)
                        styles['load' + str(level)] = curses.color_pair(10 + level)
                    else:
                        styles['load' + str(level)] = curses.color_pair(
                            3 if level == 0 else 2 if level < 8 else 5
                        )
                styles['device'] |= curses.color_pair(3)
                styles['chart'] |= curses.color_pair(4)
                styles['high'] |= curses.color_pair(5)
                styles['cpu'] |= curses.color_pair(1)
                styles['warning'] |= curses.color_pair(2)
            except curses.error:
                pass
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        curses.raw()
        if hasattr(curses, 'set_escdelay'):
            try:
                curses.set_escdelay(max(1, int(os.environ.get('ESCDELAY', '25'))))
            except ValueError:
                curses.set_escdelay(25)
        window.timeout(25)
        window.keypad(True)
        next_draw, redraw = 0.0, True
        drawn_frame = drawn_inspection = drawn_size = drawn_error = None
        while True:
            frame = sampler.poll(dashboard.selected_key(sampler.frame))
            token, request = dashboard.inspect_request(frame)
            dashboard.inspection = inspector.poll(token, request)
            height, width = window.getmaxyx()
            # Keep input/collector polling fast, but rebuild idle clocks and
            # freshness labels at most four times a second. Events draw now.
            if (
                redraw
                or frame is not drawn_frame
                or dashboard.inspection is not drawn_inspection
                or (height, width) != drawn_size
                or sampler.error != drawn_error
                or time.monotonic() >= next_draw
            ):
                window.erase()
                lines = dashboard.lines(frame, width, height, sampler.error)
                background_styles = (
                    {name: (attr & ~curses.A_BOLD) | curses.A_DIM for name, attr in styles.items()}
                    if dashboard.dialog_lines else styles
                )
                for y, line in enumerate(lines[:height]):
                    try:
                        draw_line(window, y, line, width, background_styles)
                    except curses.error:
                        pass
                x, top = dashboard.dialog_position
                for offset, line in enumerate(dashboard.dialog_lines):
                    try:
                        draw_line(window, top + offset, line, width, styles, x=x)
                    except curses.error:
                        pass
                window.noutrefresh()
                curses.doupdate()
                drawn_frame, drawn_inspection = frame, dashboard.inspection
                drawn_size, drawn_error = (height, width), sampler.error
                next_draw = time.monotonic() + 0.25
            key = window.getch()
            redraw = key != -1 or dashboard.escape_at is not None
            if not dashboard.feed(key, frame, sampler):
                break

    try:
        curses.wrapper(loop)
    finally:
        inspector.close()
