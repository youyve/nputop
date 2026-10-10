# SPDX-License-Identifier: GPL-3.0-only
"""CLI for the Ascend-native dashboard; module name retained for compatibility."""

from __future__ import annotations

import argparse
import os
import getpass
import json
import math
import locale
import sys
import time

from nputop.api.monitor import Sampler, diagnostic_report, healthy
from nputop.api.visibility import selection_error, visible_ids
from nputop.gui.monitor import Dashboard, run_dashboard, ascii_text
from nputop.version import __version__


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        prog='nputop',
        description='An interactive Ascend NPU process monitor.',
        epilog='Use --legacy-ui for the previous dashboard. Hardware compatibility matrix: docs/compatibility.md.',
    )
    parser.add_argument(
        '--preview', action='store_true', help='Compatibility alias for the default dashboard.'
    )
    parser.add_argument(
        '--legacy-ui',
        action='store_true',
        help='Use the original dashboard via the nputop entry point.',
    )
    parser.add_argument('--backend', choices=['auto', 'dcmi', 'smi'], default='auto')
    parser.add_argument(
        '--diagnose', action='store_true', help='Print a redacted capability report as JSON.'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Print one complete snapshot (includes process information).',
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', '-1', action='store_true')
    mode.add_argument(
        '--monitor',
        '-m',
        choices=['auto', 'compact', 'full'],
        nargs='?',
        const=None,
        default=None,
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=1.0,
        help='Target interval between sample starts in seconds (default: 1). Slow queries do not overlap.',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=15.0,
        help='Hard worker deadline in seconds, including backend initialization.',
    )
    parser.add_argument(
        '--only',
        '-o',
        type=int,
        nargs='+',
        help='Display indices from the current inventory (not runtime logical IDs).',
    )
    parser.add_argument('--user', '-u', nargs='*')
    parser.add_argument('--pid', '-p', type=int, nargs='+')
    parser.add_argument('--ascii', '--no-unicode', '-U', action='store_true')
    parser.add_argument(
        '--readonly', action='store_true', help='Disable all process signal actions.'
    )
    parser.add_argument(
        '--version',
        '-V',
        action='version',
        version='nputop ' + __version__,
    )
    for name in ('colorful', 'light', 'force-color'):
        parser.add_argument('--' + name, action='store_true', default=None)
    parser.add_argument('--npu-util-thresh', nargs=2, type=int)
    parser.add_argument('--mem-util-thresh', nargs=2, type=int)
    parser.add_argument('--only-visible', '-ov', action='store_true')
    for name, short in [
        ('compute', '-c'),
        ('only-compute', '-C'),
        ('graphics', '-g'),
        ('only-graphics', '-G'),
    ]:
        parser.add_argument('--' + name, short, action='store_true')
    args = parser.parse_args(argv)
    settings = {part.strip().lower() for part in os.getenv('nputop_MONITOR_MODE', '').split(',')}
    args.monitor = args.monitor or next(
        (mode for mode in ('compact', 'full', 'auto') if mode in settings), 'auto'
    )
    args.colorful = (
        args.colorful
        if args.colorful is not None
        else 'colorful' in settings and 'plain' not in settings
    )
    args.light = (
        args.light if args.light is not None else 'light' in settings and 'dark' not in settings
    )
    for attr, env, default in [
        ('npu_util_thresh', 'nputop_NPU_UTILIZATION_THRESHOLDS', [10, 75]),
        ('mem_util_thresh', 'nputop_MEMORY_UTILIZATION_THRESHOLDS', [10, 80]),
    ]:
        value = getattr(args, attr)
        if value is None:
            try:
                value = list(map(int, os.environ[env].split(','))) if env in os.environ else default
            except ValueError:
                parser.error(env + ' must contain two integer thresholds')
        if len(value) != 2 or not 1 <= value[0] < value[1] <= 99:
            parser.error('--' + attr.replace('_', '-') + ' requires 1 <= low < high <= 99')
        setattr(args, attr, value)
    if args.compute or args.only_compute or args.graphics or args.only_graphics:
        parser.error(
            'Ascend backends do not expose compute/graphics process context types; this filter is unsupported'
        )
    try:
        args.visible_ids = visible_ids() if args.only_visible and args.only is None else None
    except ValueError as exc:
        parser.error(str(exc))
    try:
        '─⣿·'.encode(sys.stdout.encoding or locale.getpreferredencoding(False))
    except (UnicodeError, LookupError):
        args.ascii = True
    if not math.isfinite(args.interval) or args.interval < 0.25:
        parser.error('--interval must be finite and at least 0.25 seconds')
    if not math.isfinite(args.timeout) or args.timeout < 0.25:
        parser.error('--timeout must be finite and at least 0.25 seconds')
    if args.only is not None and any(i < 0 for i in args.only):
        parser.error('--only requires non-negative display indices')
    if args.user == []:
        args.user = [getpass.getuser()]
    return args


def main(argv=None):
    args = parse_arguments(argv)
    if args.legacy_ui:
        # This module is also imported directly by earlier preview scripts.
        from nputop.cli import main as cli_main

        original = sys.argv
        try:
            sys.argv = [original[0]] + (list(argv) if argv is not None else original[1:])
            return cli_main()
        finally:
            sys.argv = original
    once = (
        args.once or args.json or args.diagnose or not sys.stdout.isatty() or not sys.stdin.isatty()
    )
    sampler = Sampler(args.backend, args.interval, args.timeout)
    try:
        if not once:
            return run_dashboard(sampler, args) or 0
        deadline = time.monotonic() + 2 * args.timeout + 3
        frame = None
        while time.monotonic() < deadline:
            frame = sampler.poll(
                '@mapping' if args.visible_ids is not None else '*' if args.diagnose else None
            )
            if frame is not None:
                break
            # Allow the first auto hard-timeout fallback to complete.
            if sampler.error and sampler.inflight is None:
                break
            time.sleep(0.02)
        error = selection_error(frame, args)
        if error:
            print('nputop: ' + error, file=sys.stderr)
            return 2
        if args.diagnose:
            print(
                json.dumps(
                    diagnostic_report(frame, sampler.error), indent=2, ensure_ascii=args.ascii
                )
            )
        elif frame is None:
            message = 'nputop: ' + (sampler.error or 'sampling deadline exceeded')
            print(ascii_text(message) if args.ascii else message, file=sys.stderr)
        elif args.json:
            dashboard = Dashboard(args)
            selected = frame
            if any(
                value is not None for value in (args.only, args.visible_ids, args.user, args.pid)
            ):
                selected = dict(
                    frame, devices=dashboard.devices(frame), processes=dashboard.processes(frame)
                )
            print(json.dumps(selected, indent=2, ensure_ascii=args.ascii))
        else:
            dashboard = Dashboard(args)
            for line in dashboard.report(frame, error=sampler.error):
                text = line.text.rstrip()
                if args.force_color:
                    color = {
                        'title': 36,
                        'section': 36,
                        'high': 31,
                        'warning': 33,
                        'device': 32,
                        'muted': 90,
                    }.get(line.style, 0)
                    text = '\033[' + str(color) + 'm' + text + '\033[0m'
                print(text)
        return 0 if frame and healthy(frame) else 1
    except KeyboardInterrupt:
        return 130
    finally:
        sampler.close()
