# This file is part of nvitop, the interactive NVIDIA-GPU process viewer.
# License: GNU GPL version 3.

# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

import threading
import time
from collections import deque
from functools import partial
from itertools import islice

from cachetools.func import ttl_cache

from nvitop.gui.library import (
    NA,
    SUPERUSER,
    USERNAME,
    Displayable,
    HostProcess,
    Selection,
    Snapshot,
    WideString,
    host,
    send_signal,
)


class TreeNode:  # pylint: disable=too-many-instance-attributes
    def __init__(self, process, children=()):
        self.process = process
        self.parent = None
        self.children = []
        self.devices = set()
        self.children_set = set()
        self.is_root = True
        self.is_last = False
        self.prefix = ''
        for child in children:
            self.add(child)

    def add(self, child):
        if child in self.children_set:
            return
        self.children.append(child)
        self.children_set.add(child)
        child.parent = self
        child.is_root = False

    def __getattr__(self, name):
        try:
            return super().__getattr__(name)
        except AttributeError:
            return getattr(self.process, name)

    def __eq__(self, other):
        return self.process._ident == other.process._ident  # pylint: disable=protected-access

    def __hash__(self):
        return hash(self.process)

    def as_snapshot(self):  # pylint: disable=too-many-branches,too-many-statements
        if not isinstance(self.process, Snapshot):
            with self.process.oneshot():
                try:
                    username = self.process.username()
                except host.PsutilError:
                    username = NA
                try:
                    command = self.process.command()
                    if len(command) == 0:
                        command = 'Zombie Process'
                except host.AccessDenied:
                    command = 'No Permissions'
                except host.PsutilError:
                    command = 'No Such Process'

                try:
                    cpu_percent = self.process.cpu_percent()
                except host.PsutilError:
                    cpu_percent = cpu_percent_string = NA
                else:
                    if cpu_percent is NA:
                        cpu_percent_string = NA
                    elif cpu_percent < 1000.0:
                        cpu_percent_string = f'{cpu_percent:.1f}%'
                    elif cpu_percent < 10000:
                        cpu_percent_string = f'{int(cpu_percent)}%'
                    else:
                        cpu_percent_string = '9999+%'

                try:
                    memory_percent = self.process.memory_percent()
                except host.PsutilError:
                    memory_percent = memory_percent_string = NA
                else:
                    if memory_percent is not NA:
                        memory_percent_string = f'{memory_percent:.1f}%'
                    else:
                        memory_percent_string = NA

                try:
                    num_threads = self.process.num_threads()
                except host.PsutilError:
                    num_threads = NA

                try:
                    running_time_human = self.process.running_time_human()
                except host.PsutilError:
                    running_time_human = NA

            self.process = Snapshot(
                real=self.process,
                pid=self.process.pid,
                username=username,
                command=command,
                cpu_percent=cpu_percent,
                cpu_percent_string=cpu_percent_string,
                memory_percent=memory_percent,
                memory_percent_string=memory_percent_string,
                num_threads=num_threads,
                running_time_human=running_time_human,
            )

        if len(self.children) > 0:
            for child in self.children:
                child.as_snapshot()
            self.children.sort(
                key=lambda node: (
                    node._gone,  # pylint: disable=protected-access
                    node.username,
                    node.pid,
                ),
            )
            for child in self.children:
                child.is_last = False
            self.children[-1].is_last = True

    def set_prefix(self, prefix=''):
        if self.is_root:
            self.prefix = ''
        else:
            self.prefix = prefix + ('└─ ' if self.is_last else '├─ ')
            prefix += '   ' if self.is_last else '│  '
        for child in self.children:
            child.set_prefix(prefix)

    @classmethod
    def merge(cls, leaves):  # pylint: disable=too-many-branches
        nodes = {}
        for process in leaves:
            if isinstance(process, Snapshot):
                process = process.real

            try:
                node = nodes[process.pid]
            except KeyError:
                node = nodes[process.pid] = cls(process)
            finally:
                try:
                    node.devices.add(process.device)
                except AttributeError:
                    pass

        queue = deque(nodes.values())
        while len(queue) > 0:
            node = queue.popleft()
            try:
                with node.process.oneshot():
                    parent_process = node.process.parent()
            except host.PsutilError:
                continue
            if parent_process is None:
                continue

            try:
                parent = nodes[parent_process.pid]
            except KeyError:
                parent = nodes[parent_process.pid] = cls(parent_process)
                queue.append(parent)
            else:
                continue
            finally:
                parent.add(node)

        cpid_map = host.reverse_ppid_map()
        for process in leaves:
            if isinstance(process, Snapshot):
                process = process.real

            node = nodes[process.pid]
            for cpid in cpid_map.get(process.pid, []):
                if cpid not in nodes:
                    nodes[cpid] = child = cls(HostProcess(cpid))
                    node.add(child)

        return sorted(filter(lambda node: node.is_root, nodes.values()), key=lambda node: node.pid)

    @staticmethod
    def freeze(roots):
        for root in roots:
            root.as_snapshot()
            root.set_prefix()

        return roots

    @staticmethod
    def flatten(roots):
        flattened = []
        stack = list(reversed(roots))
        while len(stack) > 0:
            top = stack.pop()
            flattened.append(top)
            stack.extend(reversed(top.children))
        return flattened


class TreeViewScreen(Displayable):  # pylint: disable=too-many-instance-attributes
    NAME = 'treeview'
    SNAPSHOT_INTERVAL = 0.5

    def __init__(self, win, root):
        super().__init__(win, root)

        self.selection = Selection(panel=self)
        self.x_offset = 0
        self.y_mouse = None

        self._snapshot_buffer = []
        self._snapshots = []
        self.snapshot_lock = threading.Lock()
        self._snapshot_daemon = threading.Thread(
            name='treeview-snapshot-daemon',
            target=self._snapshot_target,
            daemon=True,
        )
        self._daemon_running = threading.Event()

        self.x, self.y = root.x, root.y
        self.scroll_offset = 0
        self.width, self.height = root.width, root.height

    @property
    def display_height(self):
        return self.height - 1

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        if self._visible != value:
            self.need_redraw = True
            self._visible = value
        if self.visible:
            self._daemon_running.set()
            try:
                self._snapshot_daemon.start()
            except RuntimeError:
                pass
            self.snapshots = self.take_snapshots()
        else:
            self._daemon_running.clear()
            self.focused = False

    @property
    def snapshots(self):
        return self._snapshots

    @snapshots.setter
    def snapshots(self, snapshots):
        with self.snapshot_lock:
            self.need_redraw = self.need_redraw or len(self._snapshots) > len(snapshots)
            self._snapshots = snapshots

        if self.selection.is_set():
            identity = self.selection.identity
            self.selection.reset()
            for i, process in enumerate(snapshots):
                if process._ident[:2] == identity[:2]:  # pylint: disable=protected-access
                    self.selection.index = i
                    self.selection.process = process
                    break

    @classmethod
    def set_snapshot_interval(cls, interval):
        assert interval > 0.0
        interval = float(interval)

        cls.SNAPSHOT_INTERVAL = min(interval / 3.0, 1.0)
        cls.take_snapshots = ttl_cache(ttl=interval)(
            cls.take_snapshots.__wrapped__,  # pylint: disable=no-member
        )

    @ttl_cache(ttl=2.0)
    def take_snapshots(self):
        self.root.main_screen.process_panel.ensure_snapshots()
        snapshots = (
            self.root.main_screen.process_panel._snapshot_buffer  # pylint: disable=protected-access
        )

        roots = TreeNode.merge(snapshots)
        roots = TreeNode.freeze(roots)
        nodes = TreeNode.flatten(roots)

        snapshots = []
        for node in nodes:
            snapshot = node.process
            snapshot.username = WideString(snapshot.username)
            snapshot.prefix = node.prefix
            if len(node.devices) > 0:
                snapshot.devices = 'GPU ' + ','.join(
                    dev.display_index
                    for dev in sorted(node.devices, key=lambda device: device.tuple_index)
                )
            else:
                snapshot.devices = 'Host'
            snapshots.append(snapshot)

        with self.snapshot_lock:
            self._snapshot_buffer = snapshots

        return snapshots

    def _snapshot_target(self):
        while True:
            self._daemon_running.wait()
            self.take_snapshots()
            time.sleep(self.SNAPSHOT_INTERVAL)

    def update_size(self, termsize=None):
        n_term_lines, n_term_cols = termsize = super().update_size(termsize=termsize)

        self.width = n_term_cols - self.x
        self.height = n_term_lines - self.y

        return termsize

    def poke(self):
        if self._daemon_running.is_set():
            self.snapshots = self._snapshot_buffer

        self.selection.within_window = False
        if len(self.snapshots) > 0 and self.selection.is_set():
            for i, process in enumerate(self.snapshots):
                y = self.y + 1 - self.scroll_offset + i
                if self.selection.is_same_on_host(process):
                    self.selection.within_window = (
                        1 <= y - self.y < self.height and self.width >= 79
                    )
                    if not self.selection.within_window:
                        if y < self.y + 1:
                            self.scroll_offset -= self.y + 1 - y
                        elif y >= self.y + self.height:
                            self.scroll_offset += y - self.y - self.height + 1
                    self.scroll_offset = max(
                        min(len(self.snapshots) - self.display_height, self.scroll_offset),
                        0,
                    )
                    break
        else:
            self.scroll_offset = 0

        super().poke()

    def draw(self):  # pylint: disable=too-many-statements,too-many-locals
        self.color_reset()

        pid_width = max(3, max((len(str(process.pid)) for process in self.snapshots), default=3))
        username_width = max(
            4,
            max((len(process.username) for process in self.snapshots), default=4),
        )
        device_width = max(6, max((len(process.devices) for process in self.snapshots), default=6))
        num_threads_width = max(
            4,
            max((len(str(process.num_threads)) for process in self.snapshots), default=4),
        )
        time_width = max(
            4,
            max((len(process.running_time_human) for process in self.snapshots), default=4),
        )

        header = '  '.join(
            [
                'PID'.rjust(pid_width),
                'USER'.ljust(username_width),
                'DEVICE'.rjust(device_width),
                'NLWP'.rjust(num_threads_width),
                '%CPU',
                '%MEM',
                'TIME'.rjust(time_width),
                'COMMAND',
            ],
        )
        command_offset = len(header) - 7
        if self.x_offset < command_offset:
            self.addstr(
                self.y,
                self.x,
                header[self.x_offset : self.x_offset + self.width].ljust(self.width),
            )
        else:
            self.addstr(self.y, self.x, 'COMMAND'.ljust(self.width))
        self.color_at(self.y, self.x, width=self.width, fg='cyan', attr='bold | reverse')

        if len(self.snapshots) == 0:
            self.addstr(
                self.y + 1,
                self.x,
                'No running GPU processes found{}.'.format(' (in WSL)' if host.WSL else ''),
            )
            return

        hint = True
        if self.y_mouse is not None:
            self.selection.reset()
            hint = False

        self.selection.within_window = False
        processes = islice(
            self.snapshots,
            self.scroll_offset,
            self.scroll_offset + self.display_height,
        )
        for y, process in enumerate(processes, start=self.y + 1):
            prefix_length = len(process.prefix)
            line = '{}  {}  {}  {} {:>5} {:>5}  {}  {}{}'.format(
                str(process.pid).rjust(pid_width),
                process.username.ljust(username_width),
                process.devices.rjust(device_width),
                str(process.num_threads).rjust(num_threads_width),
                process.cpu_percent_string.replace('%', ''),
                process.memory_percent_string.replace('%', ''),
                process.running_time_human.rjust(time_width),
                process.prefix,
                process.command,
            )

            line = str(WideString(line)[self.x_offset :].ljust(self.width)[: self.width])
            self.addstr(y, self.x, line)

            prefix_length -= max(0, self.x_offset - command_offset)
            if prefix_length > 0:
                self.color_at(
                    y,
                    self.x + max(0, command_offset - self.x_offset),
                    width=prefix_length,
                    fg='green',
                    attr='bold',
                )

            if y == self.y_mouse:
                self.selection.process = process
                hint = True

            owned = str(process.username) == USERNAME or SUPERUSER
            if self.selection.is_same_on_host(process):
                self.color_at(
                    y,
                    self.x,
                    width=self.width,
                    fg='yellow' if self.selection.is_tagged(process) else 'green',
                    attr='bold | reverse',
                )
                self.selection.within_window = 1 <= y - self.y < self.height and self.width >= 79
            elif self.selection.is_tagged(process):
                self.color_at(
                    y,
                    self.x,
                    width=self.width,
                    fg='yellow',
                    attr='bold' if owned else 'bold | dim',
                )
            elif not owned:
                self.color_at(y, self.x, width=self.width, attr='dim')

        if not hint:
            self.selection.clear()

        self.color(fg='cyan', attr='bold | reverse')
        text_offset = self.x + self.width - 47
        if len(self.selection.tagged) > 0 or (
            self.selection.owned() and self.selection.within_window
        ):
            self.addstr(self.y, text_offset - 1, ' (Press ^C(INT)/T(TERM)/K(KILL) to send signals)')
            self.color_at(
                self.y,
                text_offset + 7,
                width=2,
                fg='cyan',
                bg='yellow',
                attr='bold | italic | reverse',
            )
            self.color_at(
                self.y,
                text_offset + 10,
                width=3,
                fg='cyan',
                bg='red',
                attr='bold | reverse',
            )
            self.color_at(
                self.y,
                text_offset + 15,
                width=1,
                fg='cyan',
                bg='yellow',
                attr='bold | italic | reverse',
            )
            self.color_at(
                self.y,
                text_offset + 17,
                width=4,
                fg='cyan',
                bg='red',
                attr='bold | reverse',
            )
            self.color_at(
                self.y,
                text_offset + 23,
                width=1,
                fg='cyan',
                bg='yellow',
                attr='bold | italic | reverse',
            )
            self.color_at(
                self.y,
                text_offset + 25,
                width=4,
                fg='cyan',
                bg='red',
                attr='bold | reverse',
            )

    def finalize(self):
        self.y_mouse = None
        super().finalize()

    def destroy(self):
        super().destroy()
        self._daemon_running.clear()

    def press(self, key):
        self.root.keymaps.use_keymap('treeview')
        self.root.press(key)

    def click(self, event):
        if event.pressed(1) or event.pressed(3) or event.clicked(1) or event.clicked(3):
            self.y_mouse = event.y
            return True

        direction = event.wheel_direction()
        if event.shift():
            self.x_offset = max(0, self.x_offset + 2 * direction)
        else:
            self.selection.move(direction=direction)
        return True

    def init_keybindings(self):
        def tree_left():
            self.x_offset = max(0, self.x_offset - 5)

        def tree_right():
            self.x_offset += 5

        def tree_begin():
            self.x_offset = 0

        def select_move(direction):
            self.selection.move(direction=direction)

        def select_clear():
            self.selection.clear()

        def tag():
            self.selection.tag()
            select_move(direction=+1)

        keymaps = self.root.keymaps

        keymaps.bind('treeview', '<Left>', tree_left)
        keymaps.copy('treeview', '<Left>', '<A-h>')
        keymaps.bind('treeview', '<Right>', tree_right)
        keymaps.copy('treeview', '<Right>', '<A-l>')
        keymaps.bind('treeview', '<C-a>', tree_begin)
        keymaps.copy('treeview', '<C-a>', '^')
        keymaps.bind('treeview', '<Up>', partial(select_move, direction=-1))
        keymaps.copy('treeview', '<Up>', '<S-Tab>')
        keymaps.copy('treeview', '<Up>', '<A-k>')
        keymaps.copy('treeview', '<Up>', '<PageUp>')
        keymaps.copy('treeview', '<Up>', '[')
        keymaps.bind('treeview', '<Down>', partial(select_move, direction=+1))
        keymaps.copy('treeview', '<Down>', '<Tab>')
        keymaps.copy('treeview', '<Down>', '<A-j>')
        keymaps.copy('treeview', '<Down>', '<PageDown>')
        keymaps.copy('treeview', '<Down>', ']')
        keymaps.bind('treeview', '<Home>', partial(select_move, direction=-(1 << 20)))
        keymaps.bind('treeview', '<End>', partial(select_move, direction=+(1 << 20)))
        keymaps.bind('treeview', '<Esc>', select_clear)
        keymaps.bind('treeview', '<Space>', tag)

        keymaps.bind('treeview', 'T', partial(send_signal, signal='terminate', panel=self))
        keymaps.bind('treeview', 'K', partial(send_signal, signal='kill', panel=self))
        keymaps.copy('treeview', 'K', 'k')
        keymaps.bind('treeview', '<C-c>', partial(send_signal, signal='interrupt', panel=self))
        keymaps.copy('treeview', '<C-c>', 'I')
