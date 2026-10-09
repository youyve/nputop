#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only hardware acceptance. No driver configuration or workload signals."""

import argparse
import json
import math
import os
import platform
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nputop.api.monitor import Sampler, diagnostic_report, numeric, host_pid_namespace
from nputop.version import __version__


def snapshot(backend):
    sampler = Sampler(backend, interval=1, timeout=10)
    try:
        deadline = time.monotonic() + 24
        while time.monotonic() < deadline:
            frame = sampler.poll('@mapping')
            if frame is not None:
                return frame, sampler.error
            if sampler.error and sampler.inflight is None:
                break
            time.sleep(0.02)
        return None, sampler.error or 'No snapshot'
    finally:
        sampler.close()


def assess(frame, error):
    result = diagnostic_report(frame, error)
    if frame is None:
        result['acceptance'] = 'unavailable'
        return result
    checks = []
    for row in frame['devices']:
        metrics = row['metrics']
        used, total = (metrics[k].get('value') for k in ('memory_used', 'memory_total'))
        checks.append(
            {
                'card': row['card'],
                'chip': row['chip'],
                'logical_id': row.get('logical_id'),
                'chip_physical_id': row.get('chip_physical_id'),
                'id_source': row.get('id_source'),
                'memory_range_valid': numeric(used)
                and numeric(total)
                and 0 <= used <= total
                and total > 0,
                'utilization_range_valid': all(
                    not numeric(m.get('value')) or 0 <= m['value'] <= 100
                    for k, m in metrics.items()
                    if k in ('aicore', 'npu_overall')
                ),
            }
        )
    result['checks'] = checks
    result['process_check'] = {
        'namespace_verified': host_pid_namespace() is not None,
        'entries': len(frame['processes']),
        'verified_entries': sum(p.get('signal_allowed') is True for p in frame['processes']),
        'device_membership_valid': all(
            p['device'] in {d['key'] for d in frame['devices']} for p in frame['processes']
        ),
    }
    result['acceptance'] = (
        'observed'
        if checks and all(c['memory_range_valid'] and c['utilization_range_valid'] for c in checks)
        else 'needs-review'
    )
    return result


def compare(a, b):
    if not a or not b:
        return {'state': 'unavailable'}
    ad, bd = ({d['key']: d for d in f['devices']} for f in (a, b))
    pairs = sorted(set(ad) & set(bd))
    capacities = all(
        ad[k]['metrics']['memory_total']['value'] == bd[k]['metrics']['memory_total']['value']
        for k in pairs
    )
    ap, bp = ({(p['device'], p['pid']) for p in f['processes']} for f in (a, b))
    return dict(
        state='observed',
        topology_equal=set(ad) == set(bd),
        memory_capacity_equal=bool(pairs) and capacities,
        process_membership_equal=ap == bp,
        process_counts=[len(ap), len(bp)],
        note='Different sampling times; process churn and dynamic utilization require local review. No PIDs exported.',
    )


def benchmark(root, count):
    import fcntl
    import pty
    import select
    import signal
    import struct
    import subprocess
    import termios

    result = {}
    for backend in ('dcmi', 'smi'):
        runs = []
        for _ in range(count):
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 48, 160, 0, 0))
            env = dict(
                os.environ, TERM='xterm-256color', PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1'
            )
            start = time.monotonic()
            process = subprocess.Popen(
                [sys.executable, '-m', 'nputop', '--preview', '--backend', backend, '--readonly'],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=str(root),
                env=env,
                start_new_session=True,
            )
            os.close(slave)
            seen, first, quit_at = b'', None, None
            try:
                while time.monotonic() - start < 25 and process.poll() is None:
                    if select.select([master], [], [], 0.01)[0]:
                        try:
                            seen = (seen + os.read(master, 65536))[-262144:]
                        except OSError:
                            break
                    if first is None and b'Memory used / total' in seen:
                        first = time.monotonic() - start
                        quit_at = time.monotonic()
                        os.write(master, b'q')
                if process.poll() is None:
                    process.wait(timeout=1)
                runs.append(
                    dict(
                        first_frame_s=first,
                        quit_s=time.monotonic() - quit_at if quit_at else None,
                        exit_code=process.returncode,
                    )
                )
            except subprocess.TimeoutExpired:
                runs.append(dict(first_frame_s=first, quit_s=None, exit_code=None))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                os.close(master)

        def p95(field):
            values = sorted(r[field] for r in runs if r[field] is not None)
            return values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None

        result[backend] = dict(
            runs=runs, first_frame_p95_s=p95('first_frame_s'), quit_p95_s=p95('quit_s')
        )
    return result


def signal_test():
    """Opt-in, only signal a child that this function itself created."""
    import curses
    import subprocess
    import psutil
    from types import SimpleNamespace
    from nputop.api.monitor import BackendManager
    from nputop.gui.monitor import Dashboard

    if host_pid_namespace() is None:
        return {'state': 'skipped', 'reason': 'initial Linux PID namespace is unverified'}
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        rows = [dict(pid=child.pid, device='test:owned', memory=0)]
        BackendManager('dcmi')._enrich_processes(rows)
        # A synthetic device only for our owned child; never substitute real workload PIDs.
        frame = dict(
            sampled_at=time.monotonic(), devices=[dict(id=0, key='test:owned')], processes=rows
        )
        args = SimpleNamespace(
            monitor='compact',
            interval=1,
            only=None,
            user=None,
            pid=None,
            ascii=True,
            readonly=False,
        )
        screen = Dashboard(args)
        screen.handle(curses.KEY_HOME, frame, SimpleNamespace(error=None))
        screen.handle(ord('T'), frame, SimpleNamespace(error=None))
        screen.handle(ord('y'), frame, SimpleNamespace(error=None))
        return {'state': 'passed' if child.wait(timeout=3) == -15 else 'failed'}
    finally:
        if child.poll() is None:
            psutil.Process(child.pid).kill()
            child.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--benchmark', type=int, default=0, metavar='RUNS_PER_BACKEND')
    parser.add_argument('--benchmark-root', type=Path, default=ROOT)
    parser.add_argument(
        '--signal-test', action='store_true', help='Only signal a child created by this script'
    )
    args = parser.parse_args()
    if not 0 <= args.benchmark <= 100:
        parser.error('--benchmark must be between 0 and 100')
    report = dict(
        schema=1,
        nputop=__version__,
        python=platform.python_version(),
        os=platform.system(),
        kernel=platform.release(),
        architecture=platform.machine(),
        validation='observations-only; maintainer review required',
    )
    if args.benchmark:
        report['benchmark'] = benchmark(args.benchmark_root, args.benchmark)
    else:
        frames = {}
        report['backends'] = {}
        for backend in ('dcmi', 'smi', 'auto'):
            value, error = snapshot(backend)
            frames[backend] = value
            report['backends'][backend] = assess(value, error)
        report['comparison'] = compare(frames['dcmi'], frames['smi'])
    if args.signal_test:
        report['owned_child_signal'] = signal_test()
    output = json.dumps(report, ensure_ascii=True, indent=2) + '\n'
    if args.output:
        args.output.write_text(output, encoding='utf-8')
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
