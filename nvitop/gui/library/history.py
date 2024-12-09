# This file is part of nvitop, the interactive NVIDIA-GPU process viewer.
# License: GNU GPL version 3.

# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

import functools
import itertools
import threading
import time
from collections import deque

from nvitop.api import NA


BOUND_UPDATE_INTERVAL = 1.0

# fmt: off
VALUE2SYMBOL_UP = {
    (0, 0): ' ', (0, 1): '⢀', (0, 2): '⢠', (0, 3): '⢰', (0, 4): '⢸',
    (1, 0): '⡀', (1, 1): '⣀', (1, 2): '⣠', (1, 3): '⣰', (1, 4): '⣸',
    (2, 0): '⡄', (2, 1): '⣄', (2, 2): '⣤', (2, 3): '⣴', (2, 4): '⣼',
    (3, 0): '⡆', (3, 1): '⣆', (3, 2): '⣦', (3, 3): '⣶', (3, 4): '⣾',
    (4, 0): '⡇', (4, 1): '⣇', (4, 2): '⣧', (4, 3): '⣷', (4, 4): '⣿',
}
VALUE2SYMBOL_DOWN = {
    (0, 0): ' ', (0, 1): '⠈', (0, 2): '⠘', (0, 3): '⠸', (0, 4): '⢸',
    (1, 0): '⠁', (1, 1): '⠉', (1, 2): '⠙', (1, 3): '⠹', (1, 4): '⢹',
    (2, 0): '⠃', (2, 1): '⠋', (2, 2): '⠛', (2, 3): '⠻', (2, 4): '⢻',
    (3, 0): '⠇', (3, 1): '⠏', (3, 2): '⠟', (3, 3): '⠿', (3, 4): '⢿',
    (4, 0): '⡇', (4, 1): '⡏', (4, 2): '⡟', (4, 3): '⡿', (4, 4): '⣿',
}
# fmt: on
SYMBOL2VALUE_UP = {v: k for k, v in VALUE2SYMBOL_UP.items()}
SYMBOL2VALUE_DOWN = {v: k for k, v in VALUE2SYMBOL_DOWN.items()}
PAIR2SYMBOL_UP = {
    (s1, s2): VALUE2SYMBOL_UP[(SYMBOL2VALUE_UP[s1][-1], SYMBOL2VALUE_UP[s2][0])]
    for s1, s2 in itertools.product(SYMBOL2VALUE_UP, repeat=2)
}
PAIR2SYMBOL_DOWN = {
    (s1, s2): VALUE2SYMBOL_DOWN[(SYMBOL2VALUE_DOWN[s1][-1], SYMBOL2VALUE_DOWN[s2][0])]
    for s1, s2 in itertools.product(SYMBOL2VALUE_DOWN, repeat=2)
}
GRAPH_SYMBOLS = ''.join(
    sorted(set(itertools.chain(VALUE2SYMBOL_UP.values(), VALUE2SYMBOL_DOWN.values()))),
).replace(' ', '')


def grouped(iterable, size, fillvalue=None):
    yield from itertools.zip_longest(*([iter(iterable)] * size), fillvalue=fillvalue)


class HistoryGraph:  # pylint: disable=too-many-instance-attributes
    MAX_WIDTH = 1024

    # pylint: disable-next=too-many-arguments
    def __init__(
        self,
        upperbound,
        width,
        height,
        *,
        format='{:.1f}'.format,  # pylint: disable=redefined-builtin
        max_format=None,
        baseline=0.0,
        dynamic_bound=False,
        min_bound=None,
        init_bound=None,
        upsidedown=False,
    ):
        assert baseline < upperbound

        self.format = format
        if max_format is None:
            max_format = format
        self.max_format = max_format

        if dynamic_bound:
            if min_bound is None:
                min_bound = baseline + 0.1 * (upperbound - baseline)
            if init_bound is None:
                init_bound = upperbound
        else:
            assert min_bound is None
            assert init_bound is None
            min_bound = init_bound = upperbound
        self.baseline = baseline
        self.min_bound = min_bound
        self.max_bound = upperbound
        self.bound = init_bound
        self.next_bound_update_at = time.monotonic()
        self._width = width
        self._height = height

        self.maxlen = 2 * self.width + 1
        self.history = deque(
            [self.baseline - 0.1] * (2 * self.MAX_WIDTH + 1),
            maxlen=(2 * self.MAX_WIDTH + 1),
        )
        self.reversed_history = deque([self.baseline - 0.1] * self.maxlen, maxlen=self.maxlen)
        self._max_value_maintainer = deque([self.baseline - 0.1] * self.maxlen, maxlen=self.maxlen)
        self.last_retval = None

        self.graph = []
        self.last_graph = []
        self.upsidedown = upsidedown
        if upsidedown:
            self.value2symbol = VALUE2SYMBOL_DOWN
            self.pair2symbol = PAIR2SYMBOL_DOWN
        else:
            self.value2symbol = VALUE2SYMBOL_UP
            self.pair2symbol = PAIR2SYMBOL_UP

        self.write_lock = threading.Lock()
        self.remake_lock = threading.Lock()
        self.remake_graph()

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, value):
        if self._width != value:
            assert isinstance(value, int)
            assert value >= 1
            self._width = value
            with self.write_lock:
                self.maxlen = 2 * self.width + 1
                self.reversed_history = deque(
                    (self.baseline - 0.1,) * self.maxlen,
                    maxlen=self.maxlen,
                )
                self._max_value_maintainer = deque(
                    (self.baseline - 0.1,) * self.maxlen,
                    maxlen=self.maxlen,
                )
                for history in itertools.islice(
                    self.history,
                    max(0, self.history.maxlen - self.maxlen),
                    self.history.maxlen,
                ):
                    if self.reversed_history[-1] == self._max_value_maintainer[0]:
                        self._max_value_maintainer.popleft()
                    while (
                        len(self._max_value_maintainer) > 0
                        and self._max_value_maintainer[-1] < history
                    ):
                        self._max_value_maintainer.pop()
                    self.reversed_history.appendleft(history)
                    self._max_value_maintainer.append(history)
                self.remake_graph()

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, value):
        if self._height != value:
            assert isinstance(value, int)
            assert value >= 1
            self._height = value
            self.remake_graph()

    @property
    def graph_size(self):
        return (self.width, self.height)

    @graph_size.setter
    def graph_size(self, value):
        width, height = value
        assert isinstance(width, int)
        assert width >= 1
        assert isinstance(height, int)
        assert height >= 1
        self._height = height
        self._width = width - 1  # trigger force remake
        self.width = width

    @property
    def last_value(self):
        return self.reversed_history[0]

    @property
    def max_value(self):
        return self._max_value_maintainer[0]

    def last_value_string(self):
        last_value = self.last_value
        if last_value >= self.baseline:
            return self.format(last_value)
        try:
            return self.format(NA)
        except ValueError:
            return NA

    __str__ = last_value_string

    def max_value_string(self):
        max_value = self.max_value
        if max_value >= self.baseline:
            return self.max_format(max_value)
        try:
            return self.max_format(NA)
        except ValueError:
            return NA

    def add(self, value):
        if value is NA:
            value = self.baseline - 0.1
        if not isinstance(value, (int, float)):
            return

        with self.write_lock:
            if self.reversed_history[-1] == self._max_value_maintainer[0]:
                self._max_value_maintainer.popleft()
            while len(self._max_value_maintainer) > 0 and self._max_value_maintainer[-1] < value:
                self._max_value_maintainer.pop()
            self.reversed_history.appendleft(value)
            self._max_value_maintainer.append(value)
            self.history.append(value)

            new_bound = self.baseline + 1.25 * (self.max_value - self.baseline)
            new_bound = min(max(new_bound, self.min_bound), self.max_bound)
            timestamp = time.monotonic()
            if new_bound != self.bound and self.next_bound_update_at <= timestamp:
                self.bound = new_bound
                self.remake_graph()
                self.next_bound_update_at = timestamp + BOUND_UPDATE_INTERVAL
                return

            self.graph, self.last_graph = self.last_graph, self.graph
            bar = self.make_bar(self.reversed_history[1], value)  # pylint: disable=disallowed-name
            for i, (line, char) in enumerate(zip(self.graph, bar)):
                self.graph[i] = (line + char)[-self.width :]

    def remake_graph(self):
        with self.remake_lock:
            if self.max_value >= self.baseline:
                reversed_bars = []
                for _, (value2, value1) in zip(
                    range(self.width),
                    grouped(self.reversed_history, size=2, fillvalue=self.baseline),
                ):
                    reversed_bars.append(self.make_bar(value1, value2))
                graph = list(map(''.join, zip(*reversed(reversed_bars))))

                for i, line in enumerate(graph):
                    graph[i] = line.rjust(self.width)[-self.width :]

                self.graph = graph
                self.last_graph = list(map(self.shift_line, self.graph))
            else:
                self.graph = [' ' * self.width for _ in range(self.height)]
                self.last_graph = [' ' * (self.width - 1) for _ in range(self.height)]

    def make_bar(self, value1, value2):
        if self.bound <= self.baseline:
            return [' '] * self.height

        value1 = self.height * min((value1 - self.baseline) / (self.bound - self.baseline), 1.0)
        value2 = self.height * min((value2 - self.baseline) / (self.bound - self.baseline), 1.0)
        if value1 >= 0.0:
            value1 = max(value1, 0.2)
        if value2 >= 0.0:
            value2 = max(value2, 0.2)
        # pylint: disable=disallowed-name,invalid-name
        bar = []
        for h in range(self.height):
            s1 = min(max(round(5 * (value1 - h)), 0), 4)
            s2 = min(max(round(5 * (value2 - h)), 0), 4)
            bar.append(self.value2symbol[(s1, s2)])
        if not self.upsidedown:
            bar.reverse()
        return bar

    def shift_line(self, line):
        return ''.join(map(self.pair2symbol.get, zip(line, line[1:])))

    def __getitem__(self, item):
        return self.reversed_history[item]

    def hook(self, func, get_value=None):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            self.last_retval = retval = value = func(*args, **kwargs)
            if get_value is not None:
                value = get_value(retval)
            self.add(value)
            return retval

        wrapped.history = self
        return wrapped

    __call__ = hook


class BufferedHistoryGraph(HistoryGraph):
    # pylint: disable-next=too-many-arguments
    def __init__(
        self,
        upperbound,
        width,
        height,
        *,
        format='{:.1f}'.format,  # pylint: disable=redefined-builtin
        max_format=None,
        baseline=0.0,
        dynamic_bound=False,
        upsidedown=False,
        min_bound=None,
        init_bound=None,
        interval=1.0,
    ):
        assert interval > 0.0
        super().__init__(
            upperbound,
            width,
            height,
            format=format,
            max_format=max_format,
            baseline=baseline,
            dynamic_bound=dynamic_bound,
            min_bound=min_bound,
            init_bound=init_bound,
            upsidedown=upsidedown,
        )

        self.interval = interval
        self.start_time = time.monotonic()
        self.last_update_time = self.start_time
        self.buffer = []

    @property
    def last_value(self):
        last_value = super().last_value
        if last_value < self.baseline and len(self.buffer) > 0:
            return sum(self.buffer) / len(self.buffer)
        return last_value

    def add(self, value):
        if value is NA:
            value = self.baseline - 0.1
        if not isinstance(value, (int, float)):
            return

        timestamp = time.monotonic()
        timedelta = timestamp - self.last_update_time
        if len(self.buffer) > 0 and timedelta >= self.interval:
            new_value = sum(self.buffer) / len(self.buffer)
            self.buffer.clear()
            last_value = self.reversed_history[0]
            if last_value >= self.baseline:
                n_interval = int(timedelta / self.interval)
                for i in range(1, n_interval):
                    super().add(last_value + (i / n_interval) * (new_value - last_value))
            super().add(new_value)

            self.last_update_time += (timedelta // self.interval) * self.interval
        self.buffer.append(value)
