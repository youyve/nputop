# SPDX-License-Identifier: GPL-3.0-only
"""Snapshot-only process pages and their bounded inspection requests."""

from __future__ import annotations

from collections import defaultdict, deque

from nputop.api.inspection import identity
from nputop.api.monitor import clean, numeric
from nputop.gui.library.widestring import WideString


class ProcessViews:
    def init_views(self):
        self.view = 'main'
        self.view_target = None
        self.view_generation = 0
        self.view_offset = self.view_horizontal = 0
        self.view_limit = self.view_width = 0
        self.inspection = None
        self.tree_rows = []
        self.tree_index = 0
        self.tree_selected = None
        self.process_history = defaultdict(lambda: deque(maxlen=600))
        self.process_history_at = None
        self.tags = {}
        self.selection_active = False
        self.sort_prefix = False
        self.screen_offset = 0
        self.screen_limit = 0
        self.page_size = 8
        self.escape_at = None
        self.modal_offset = 0
        self.modal_limit = 0

    def chosen_process(self, frame):
        if self.view == 'tree':
            return (
                self.tree_rows[min(self.tree_index, len(self.tree_rows) - 1)]
                if self.tree_rows and self.selection_active
                else None
            )
        if self.view != 'main':
            return self.view_target
        rows = self.processes(frame)
        if not self.selection_active or self.focus != 'processes' or not rows:
            return None
        if self.selected_process is not None:
            for row in rows:
                if (row['device'], row['pid'], row.get('created')) == self.selected_process:
                    return row
            return None  # A disappearing row must not silently select a new signal target.
        return rows[min(self.process_index, len(rows) - 1)]

    def open_view(self, view, frame):
        target = self.chosen_process(frame)
        if target is None:
            self.notice = 'Select a process first.'
            return
        if not target.get('created') or not target.get('pid_namespace'):
            self.notice = 'Process identity/namespace is unverified; host inspection unavailable.'
            return
        self.view = view
        self.view_target = dict(target)
        self.view_generation += 1
        self.inspection = None
        self.view_offset = self.view_horizontal = self.tree_index = 0
        self.tree_selected = identity(target) if view == 'tree' else None
        self.process_history.clear()
        self.process_history_at = None
        self.notice = ''

    def inspect_request(self, frame):
        if self.view == 'main' or self.view_target is None:
            return None, None
        sources = []
        if self.view == 'tree':
            sources = [
                {key: row.get(key) for key in ('pid', 'created', 'pid_namespace', 'device')}
                for row in self.processes(frame)
            ]
        return (self.view, identity(self.view_target), self.view_generation), (
            self.view,
            self.view_target,
            sources,
        )

    def page_lines(self, frame, width, height):
        from nputop.gui.monitor import Line, dot_history, size, elapsed

        self.page_size = max(1, height - 7)
        target = self.view_target
        title = {'tree': 'Process tree', 'environ': 'Environment', 'metrics': 'Process metrics'}[
            self.view
        ]
        result = [
            Line(' nputop / ' + title, 'title'),
            Line(' Esc/q back · h help · r refresh', 'muted'),
        ]
        result.append(
            Line(
                ' PID {} · {} · {}'.format(
                    target['pid'], target.get('user', '?'), clean(target.get('command', ''))
                )
            )
        )
        result.append(Line(('=' if self.args.ascii else '═') * max(1, width - 1), 'border'))
        data = self.inspection
        self.tree_rows = []
        if data is None:
            body = [Line(' Collecting process information...', 'muted')]
        elif data['state'] != 'ok':
            body = [Line(' ' + data.get('reason', 'Process unavailable'), 'warning')]
        elif self.view == 'environ':
            body = [Line(' ' + text) for text in data['entries']] or [
                Line(' Environment is empty.', 'muted')
            ]
        elif self.view == 'tree':
            nodes = {p['pid']: p for p in data['nodes']}
            children = defaultdict(list)
            for node in nodes.values():
                children[node['ppid'] if node['ppid'] in nodes else None].append(node)
            ordered, visited = [], set()

            def visit(row, depth):
                if row['pid'] in visited:
                    return
                visited.add(row['pid'])
                ordered.append((row, depth))
                for child in sorted(children[row['pid']], key=lambda p: p['pid']):
                    visit(child, min(64, depth + 1))

            for row in sorted(children[None], key=lambda p: p['pid']):
                visit(row, 0)
            for row in nodes.values():
                visit(row, 0)  # Defensive against a reparenting race/cycle.
            self.tree_rows = [row for row, _ in ordered]
            if self.tree_selected is not None:
                match = next(
                    (i for i, p in enumerate(self.tree_rows) if identity(p) == self.tree_selected),
                    None,
                )
                if match is not None:
                    self.tree_index = match
                else:
                    self.selection_active = False
            self.tree_index = min(self.tree_index, max(0, len(ordered) - 1))
            if self.tree_rows and self.selection_active:
                self.tree_selected = identity(self.tree_rows[self.tree_index])
            self.view_offset = min(self.view_offset, self.tree_index)
            self.view_offset = max(self.view_offset, self.tree_index - self.page_size + 1)
            body = []
            for i, (row, depth) in enumerate(ordered):
                chips = ','.join(row['devices']) or 'host'
                tagged = '*' if identity(row) in self.tags else ' '
                body.append(
                    Line(
                        f"{tagged}{row['pid']:>8} {chips:<12} "
                        + '  ' * depth
                        + ('+- ' if self.args.ascii else '└─ ')
                        + row['command'],
                        'selected' if self.selection_active and i == self.tree_index else 'normal',
                        ('tree', i),
                    )
                )
            if not ordered:
                body.append(Line(' No accessible processes; processes may have exited.', 'muted'))
            if data.get('truncated'):
                body.append(Line(' Tree truncated at 512 nodes.', 'warning'))
        else:
            matches = [p for p in self.processes(frame) if identity(p) == identity(target)]
            row = matches[0] if matches else {}
            body = [
                Line(
                    ' State {} · PPID {} · Threads {} · Elapsed {}'.format(
                        data['status'],
                        data['ppid'],
                        data['threads'],
                        elapsed(row.get('time', data.get('time'))),
                    )
                ),
                Line(
                    ' NPU memory: sum of this process on displayed chips; CPU/RSS counted once.',
                    'muted',
                ),
                Line(
                    ' Process NPU utilization / bandwidth / clock: unavailable from this backend.',
                    'muted',
                ),
            ]
            if frame and self.process_history_at != frame['sampled_at']:
                self.process_history_at = frame['sampled_at']
                valid_mem = (
                    matches
                    and frame['process_status'] == 'ok'
                    and all(numeric(p.get('memory')) for p in matches)
                )
                values = dict(
                    cpu=row.get('cpu', data.get('cpu')),
                    rss=row.get('host_rss', data.get('rss')),
                    npu=sum(p['memory'] for p in matches) if valid_mem else None,
                )
                self.process_history['time'].append(frame['sampled_at'])
                for key, value in values.items():
                    self.process_history[key].append(value)
            for key, label in [('cpu', 'CPU'), ('rss', 'Host RSS'), ('npu', 'NPU memory')]:
                values = list(self.process_history[key])
                current = values[-1] if values else None
                top = max([v for v in values if numeric(v)] + [100 if key == 'cpu' else 1])
                value_text = (
                    f'{current:.1f}%'
                    if key == 'cpu' and numeric(current)
                    else size(current)
                    if key != 'cpu'
                    else '--'
                )
                body.append(
                    Line(
                        (
                            f' {label}: {value_text}  (scale {top:.0f}%' + ')'
                            if key == 'cpu'
                            else f' {label}: {value_text}  (scale {size(top)})'
                        ),
                        'cpu' if key == 'cpu' else 'chart',
                    )
                )
                slots = [None] * max(1, width - 4)
                for at, value in zip(self.process_history['time'], values):
                    slot = len(slots) - 1 - round((frame['sampled_at'] - at) / self.args.interval)
                    if 0 <= slot < len(slots) and numeric(value):
                        slots[slot] = value / top * 100
                body.extend(
                    Line(' ' + line, 'cpu' if key == 'cpu' else 'chart')
                    for line in dot_history(slots, len(slots), 3, key == 'rss', self.args.ascii)
                )
        self.view_limit = max(0, len(body) - self.page_size)
        self.view_offset = min(self.view_limit, max(0, self.view_offset))
        self.view_width = max([len(WideString(l.text)) - width + 1 for l in body] + [0])
        self.view_horizontal = min(self.view_horizontal, self.view_width)
        result += [
            Line(str(WideString(l.text)[self.view_horizontal :]), l.style, l.target)
            for l in body[self.view_offset : self.view_offset + self.page_size]
        ]
        return [
            Line(str(WideString(l.text)[: max(1, width - 1)]), l.style, l.target) for l in result
        ]
