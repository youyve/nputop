# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, on-demand host inspection, independent of the NPU sampler."""

from __future__ import annotations

import time
import queue
import os

import psutil

from nputop.api.monitor import Sampler, _session_worker, clean, host_pid_namespace


def identity(row):
    return row.get('pid_namespace'), row['pid'], row.get('created')


def verified_process(row):
    namespace = host_pid_namespace()
    if not namespace or row.get('pid_namespace') != namespace or row.get('created') is None:
        raise RuntimeError('PID namespace or process identity is unverified; read-only')
    process = psutil.Process(row['pid'])
    if abs(process.create_time() - row['created']) > 0.001 or not process.is_running():
        raise RuntimeError('Process exited or PID was reused')
    return process


def inspect(request, cache=None):
    """Called only in the host worker. Never initializes an accelerator runtime."""
    view, target, sources = request
    process = verified_process(target)
    if view == 'environ':
        # Never include this result in normal snapshots or diagnostic exports.
        entries, remaining = [], 65536
        for key, value in sorted(process.environ().items()):
            text = clean(key + '=' + value)
            encoded = text.encode('utf-8')
            if len(encoded) > remaining:
                entries.append('[environment truncated at 64 KiB]')
                break
            entries.append(text)
            remaining -= len(encoded)
        verified_process(target)
        return {'state': 'ok', 'entries': entries}
    if view == 'metrics':
        token = identity(target)
        primed = cache is not None and token in cache
        if cache is not None:
            process = cache.get(token, process)
            cache.clear()
            cache[token] = process
        with process.oneshot():
            result = dict(
                state='ok',
                cpu=process.cpu_percent(),
                time=max(0, time.time() - target['created']),
                ppid=process.ppid(),
                rss=process.memory_info().rss,
                threads=process.num_threads(),
                status=process.status(),
                command=clean(' '.join(process.cmdline()) or process.name()),
            )
        if not primed:
            result['cpu'] = None
        verified_process(target)
        return result
    if view != 'tree':
        raise RuntimeError('Unknown inspection request')
    namespace = host_pid_namespace()
    nodes, truncated = {}, False

    def add(proc):
        if proc.pid in nodes:
            return True
        if len(nodes) >= 512:
            return False
        try:
            with proc.oneshot():
                nodes[proc.pid] = dict(
                    pid=proc.pid,
                    ppid=proc.ppid(),
                    created=proc.create_time(),
                    pid_namespace=namespace,
                    command=clean(' '.join(proc.cmdline()) or proc.name()),
                    user=proc.username(),
                    signal_allowed=True,
                    host_state='ok',
                )
            if not proc.is_running():
                nodes.pop(proc.pid, None)
        except (psutil.Error, OSError):
            return True
        return True

    for row in sources:
        try:
            leaf = verified_process(row)
            add(leaf)
            # Ancestors provide context; only descendants of NPU leaves are included.
            ancestor, seen = leaf, set()
            for _ in range(64):
                ancestor = ancestor.parent()
                if ancestor is None or ancestor.pid in seen:
                    break
                seen.add(ancestor.pid)
                if not add(ancestor):
                    truncated = True
                    break
            for child in leaf.children(recursive=True):
                if not add(child):
                    truncated = True
                    break
        except (psutil.Error, RuntimeError):
            continue
    for node in nodes.values():
        node['devices'] = sorted({r['device'] for r in sources if identity(r) == identity(node)})
    return {'state': 'ok', 'nodes': list(nodes.values()), 'truncated': truncated}


def _inspect_worker(channel, mode):
    connection, requests, parent_pid = channel
    cache = {}
    try:
        # Queue keeps a writer open in this child, so parent death cannot yield
        # EOF. A bounded wait also covers a parent lost before spawn completed.
        while os.getppid() == parent_pid:
            try:
                token, request = requests.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                result = inspect(request, cache)
                result['target'] = dict(request[1])
            except (psutil.Error, RuntimeError, OSError) as exc:
                result = {
                    'state': 'unavailable',
                    'reason': clean(type(exc).__name__ + ': ' + str(exc)),
                }
            connection.send((token, result))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class Inspector(Sampler):
    """One in-flight request, stale responses discarded, hard timeout, owned cleanup."""

    def __init__(self, timeout=3.0):
        super().__init__('host', interval=1.0, timeout=timeout)
        self.token = None
        self.sent_token = None
        self.result = None
        self.completed = False
        self.requests = None

    def _start(self):
        parent, child = self.context.Pipe(duplex=False)
        self.requests = self.context.Queue(maxsize=1)
        self.connection = parent
        self.session_ready = self.context.Event()
        self.process = self.context.Process(
            target=_session_worker,
            args=((child, self.requests, os.getpid()), 'host', self.session_ready, _inspect_worker),
            daemon=True,
        )
        self.process.start()
        child.close()

    def poll(self, token=None, request=None):
        now = time.monotonic()
        if token != self.token:
            self.token, self.result, self.completed = token, None, False
            self.next_sample = 0
        if self.connection is not None and self.connection.poll():
            try:
                received, result = self.connection.recv()
                if received == self.token:
                    result['sampled_at'] = now
                    self.result, self.completed = result, True
                    self.next_sample = now + self.interval
            except (EOFError, OSError):
                self.result = {'state': 'unavailable', 'reason': 'Host inspection worker exited'}
                self.completed = True
            self.inflight = None
        if self._exited() or (self.inflight is not None and now - self.inflight >= self.timeout):
            self._stop()
            self.result = {'state': 'unavailable', 'reason': 'Host inspection timed out or exited'}
            self.completed = True
            self.next_sample = now + 5
        if (
            token is not None
            and request is not None
            and self.inflight is None
            and now >= self.next_sample
        ):
            if not (request[0] == 'environ' and self.completed):
                if self.process is None:
                    self._start()
                try:
                    self.requests.put_nowait((token, request))
                    self.sent_token, self.inflight = token, now
                except (BrokenPipeError, EOFError, OSError, queue.Full):
                    self._stop()
                    self.result = {
                        'state': 'unavailable',
                        'reason': 'Host inspection connection failed',
                    }
                    self.completed = True
                    self.next_sample = now + 5
        return self.result

    def _stop(self):
        super()._stop()
        if self.requests is not None:
            self.requests.cancel_join_thread()
            self.requests.close()
            self.requests = None
