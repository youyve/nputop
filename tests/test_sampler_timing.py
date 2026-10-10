"""Sampling cadence must include query time without queuing missed work."""

from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nputop import cli, preview
from nputop.api import monitor
from test_monitor import fake_frame


class Connection:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.responses = deque()
        self.busy = False

    def send(self, request):
        assert not self.busy, 'overlapping collection requests'
        self.busy = True
        self.sent.append((self.clock[0], request))

    def poll(self):
        return bool(self.responses)

    def recv(self):
        self.busy = False
        return self.responses.popleft()


@pytest.fixture
def sampling(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(monitor.time, 'monotonic', lambda: clock[0])
    sampler = monitor.Sampler(mode='dcmi')
    connection = Connection(clock)
    sampler.process = object()
    sampler.connection = connection
    monkeypatch.setattr(sampler, '_exited', lambda: False)
    return SimpleNamespace(clock=clock, sampler=sampler, connection=connection)


@pytest.mark.parametrize('flags', [[], ['--preview']])
@pytest.mark.parametrize('interval', [None, 0.25, 2, 5])
def test_native_entry_passes_default_or_explicit_interval(monkeypatch, capsys, flags, interval):
    import sys

    argv = ['nputop'] + flags + ['--json']
    if interval is not None:
        argv += ['--interval', str(interval)]
    monkeypatch.setattr(sys, 'argv', argv)
    sampler = Mock()
    sampler.poll.return_value = fake_frame()
    factory = Mock(return_value=sampler)
    monkeypatch.setattr(preview, 'Sampler', factory)
    assert cli.main() == 0
    factory.assert_called_once_with('auto', 1.0 if interval is None else interval, 15.0)
    sampler.close.assert_called_once()
    assert capsys.readouterr().out


@pytest.mark.parametrize('interval', [0.25, 1.0, 2.0, 5.0])
def test_collection_time_counts_toward_interval(sampling, interval):
    clock, sampler, connection = sampling.clock, sampling.sampler, sampling.connection
    sampler.interval = interval
    sampler.poll()
    assert sampler.inflight == 0.0  # A zero-origin monotonic clock is valid.
    clock[0] = interval * 0.4
    frame = fake_frame()
    connection.responses.append(('frame', frame))
    assert sampler.poll() is frame
    clock[0] = interval - 0.01
    sampler.poll()
    assert len(connection.sent) == 1
    clock[0] = interval
    sampler.poll('5:0')
    assert connection.sent == [(0.0, ('collect', None)), (interval, ('collect', '5:0'))]
    assert sampler.sequence == 1


def test_slow_sample_delivers_frame_before_more_work_and_never_overlaps(sampling):
    clock, sampler, connection = sampling.clock, sampling.sampler, sampling.connection
    sampler.poll()
    for clock[0] in (0.5, 1.0, 2.0):
        sampler.poll()
    assert len(connection.sent) == 1
    clock[0] = 2.75
    frame = fake_frame()
    connection.responses.append(('frame', frame))
    assert sampler.poll() is frame
    assert sampler.inflight is None
    assert len(connection.sent) == 1  # --once/--json can close without starting another query.
    clock[0] = 2.775
    sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 2.875
    connection.responses.append(('frame', fake_frame()))
    sampler.poll()
    clock[0] = 3.5
    sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 3.775
    sampler.poll()
    assert len(connection.sent) == 3


def test_delayed_poll_skips_missed_intervals(sampling):
    clock, sampler, connection = sampling.clock, sampling.sampler, sampling.connection
    sampler.poll()
    clock[0] = 0.1
    connection.responses.append(('frame', fake_frame()))
    sampler.poll()
    clock[0] = 10.0
    for _ in range(5):
        sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 10.1
    connection.responses.append(('frame', fake_frame()))
    sampler.poll()
    sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 11.0
    sampler.poll()
    assert len(connection.sent) == 3


def test_error_backoff_is_not_shortened_by_normal_cadence(sampling):
    clock, sampler, connection = sampling.clock, sampling.sampler, sampling.connection
    sampler.poll()
    clock[0] = 0.25
    connection.responses.append(('error', 'temporary failure'))
    sampler.poll()
    clock[0] = 1.0
    sampler.poll()
    assert len(connection.sent) == 1
    clock[0] = 2.25
    sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 2.5
    connection.responses.append(('error', 'temporary failure'))
    sampler.poll()
    clock[0] = 6.49
    sampler.poll()
    assert len(connection.sent) == 2
    clock[0] = 6.5
    sampler.poll()
    clock[0] = 6.75
    connection.responses.append(('frame', fake_frame()))
    sampler.poll()
    assert sampler.error is None and sampler.failures == 0
    clock[0] = 7.5
    sampler.poll()
    assert len(connection.sent) == 4
