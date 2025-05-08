# This file is part of nputop, the interactive Ascend-NPU process viewer.
# License: GNU GPL version 3.


"""The interactive Ascend-NPU process viewer."""

import argparse
import curses
import os
import sys
import textwrap

from nputop.api import HostProcess
from nputop.gui import UI, USERNAME, Device, colored, libcurses, set_color, setlocale_utf8
from nputop.version import __version__


TTY = sys.stdin.isatty() and sys.stdout.isatty()
nputop_MONITOR_MODE = set(
    map(
        str.strip,
        os.environ.get('nputop_MONITOR_MODE', '').lower().split(','),
    ),
)


# pylint: disable=too-many-branches,too-many-statements
def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments for ``nputop``."""
    coloring_rules = '{} < th1 %% <= {} < th2 %% <= {}'.format(
        colored('light', 'green'),
        colored('moderate', 'yellow'),
        colored('heavy', 'red'),
    )

    def posfloat(argstring: str) -> float:
        num = float(argstring)
        if num <= 0:
            raise ValueError
        return num

    posfloat.__name__ = 'positive float'

    parser = argparse.ArgumentParser(
        prog='nputop',
        description='An interactive NVIDIA-NPU process viewer.',
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
    )
    parser.add_argument(
        '--help',
        '-h',
        dest='help',
        action='help',
        default=argparse.SUPPRESS,
        help='Show this help message and exit.',
    )
    parser.add_argument(
        '--version',
        '-V',
        dest='version',
        action='version',
        version=f'%(prog)s {__version__}',
        help="Show %(prog)s's version number and exit.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--once',
        '-1',
        dest='once',
        action='store_true',
        help='Report query data only once.',
    )
    mode.add_argument(
        '--monitor',
        '-m',
        dest='monitor',
        type=str,
        default=argparse.SUPPRESS,
        nargs='?',
        choices=['auto', 'full', 'compact'],
        help=(
            'Run as a resource monitor. Continuously report query data and handle user inputs.\n'
            'If the argument is omitted, the value from `nputop_MONITOR_MODE` will be used.\n'
            '(default fallback mode: auto)'
        ),
    )

    parser.add_argument(
        '--interval',
        dest='interval',
        type=posfloat,
        default=None,
        metavar='SEC',
        help='Process status update interval in seconds. (default: 2)',
    )
    parser.add_argument(
        '--ascii',
        '--no-unicode',
        '-U',
        dest='ascii',
        action='store_true',
        help='Use ASCII characters only, which is useful for terminals without Unicode support.',
    )

    coloring = parser.add_argument_group('coloring')
    coloring.add_argument(
        '--colorful',
        dest='colorful',
        action='store_true',
        help=(
            'Use gradient colors to get spectrum-like bar charts. This option is only available\n'
            'when the terminal supports 256 colors. You may need to set environment variable\n'
            '`TERM="xterm-256color"`. Note that the terminal multiplexer, such as `tmux`, may\n'
            'override the `TREM` variable.'
        ),
    )
    coloring.add_argument(
        '--force-color',
        dest='force_color',
        action='store_true',
        help='Force colorize even when `stdout` is not a TTY terminal.',
    )
    coloring.add_argument(
        '--light',
        action='store_true',
        help=(
            'Tweak visual results for light theme terminals in monitor mode.\n'
            'Set variable `nputop_MONITOR_MODE="light"` on light terminals for convenience.'
        ),
    )
    npu_thresholds = Device.NPU_UTILIZATION_THRESHOLDS
    coloring.add_argument(
        '--npu-util-thresh',
        type=int,
        nargs=2,
        choices=range(1, 100),
        metavar=('th1', 'th2'),
        help=(
            'Thresholds of NPU utilization to determine the load intensity.\n'
            'Coloring rules: {}.\n'
            '( 1 <= th1 < th2 <= 99, defaults: {} {} )'
        ).format(coloring_rules, *npu_thresholds),
    )
    memory_thresholds = Device.MEMORY_UTILIZATION_THRESHOLDS
    coloring.add_argument(
        '--mem-util-thresh',
        type=int,
        nargs=2,
        choices=range(1, 100),
        metavar=('th1', 'th2'),
        help=(
            'Thresholds of NPU memory percent to determine the load intensity.\n'
            'Coloring rules: {}.\n'
            '( 1 <= th1 < th2 <= 99, defaults: {} {} )'
        ).format(coloring_rules, *memory_thresholds),
    )

    device_filtering = parser.add_argument_group('device filtering')
    device_filtering.add_argument(
        '--only',
        '-o',
        dest='only',
        type=int,
        nargs='+',
        metavar='idx',
        help='Only show the specified devices, suppress option `--only-visible`.',
    )
    device_filtering.add_argument(
        '--only-visible',
        '-ov',
        dest='only_visible',
        action='store_true',
        help='Only show devices in the `CUDA_VISIBLE_DEVICES` environment variable.',
    )

    process_filtering = parser.add_argument_group('process filtering')
    process_filtering.add_argument(
        '--compute',
        '-c',
        dest='compute',
        action='store_true',
        help="Only show NPU processes with the compute context. (type: 'C' or 'C+G')",
    )
    process_filtering.add_argument(
        '--only-compute',
        '-C',
        dest='only_compute',
        action='store_true',
        help="Only show NPU processes exactly with the compute context. (type: 'C' only)",
    )
    process_filtering.add_argument(
        '--graphics',
        '-g',
        dest='graphics',
        action='store_true',
        help="Only show NPU processes with the graphics context. (type: 'G' or 'C+G')",
    )
    process_filtering.add_argument(
        '--only-graphics',
        '-G',
        dest='only_graphics',
        action='store_true',
        help="Only show NPU processes exactly with the graphics context. (type: 'G' only)",
    )
    process_filtering.add_argument(
        '--user',
        '-u',
        dest='user',
        type=str,
        nargs='*',
        metavar='USERNAME',
        help='Only show processes of the given users (or `$USER` for no argument).',
    )
    process_filtering.add_argument(
        '--pid',
        '-p',
        dest='pid',
        type=int,
        nargs='+',
        metavar='PID',
        help='Only show processes of the given PIDs.',
    )

    args = parser.parse_args()

    if args.interval is not None and args.interval < 0.25:
        parser.error(
            f'the interval {args.interval:0.2g}s is too short, which may cause performance issues. '
            f'Expected 1/4 or higher.',
        )

    if not args.colorful:
        args.colorful = 'colorful' in nputop_MONITOR_MODE and 'plain' not in nputop_MONITOR_MODE
    if not args.light:
        args.light = 'light' in nputop_MONITOR_MODE and 'dark' not in nputop_MONITOR_MODE
    if args.user is not None and len(args.user) == 0:
        args.user.append(USERNAME)
    if args.npu_util_thresh is None:
        try:
            npu_util_thresh = list(
                map(int, os.getenv('nputop_NPU_UTILIZATION_THRESHOLDS', '').split(',')),
            )[:2]
        except ValueError:
            pass
        else:
            if (
                len(npu_util_thresh) == 2
                and min(npu_util_thresh) > 0
                and max(npu_util_thresh) < 100
            ):
                args.npu_util_thresh = npu_util_thresh
    if args.mem_util_thresh is None:
        try:
            mem_util_thresh = list(
                map(int, os.getenv('nputop_MEMORY_UTILIZATION_THRESHOLDS', '').split(',')),
            )[:2]
        except ValueError:
            pass
        else:
            if (
                len(mem_util_thresh) == 2
                and min(mem_util_thresh) > 0
                and max(mem_util_thresh) < 100
            ):
                args.mem_util_thresh = mem_util_thresh

    return args


# pylint: disable-next=too-many-branches,too-many-statements,too-many-locals
def main() -> int:
    """Main function for ``nputop`` CLI."""
    args = parse_arguments()

    if args.force_color:
        set_color(True)

    messages = []
    if args.once and hasattr(args, 'monitor'):
        messages.append('ERROR: Both `--once` and `--monitor` switches are on.')
        del args.monitor

    if not args.once and not hasattr(args, 'monitor') and TTY:
        args.monitor = None

    if hasattr(args, 'monitor') and not TTY:
        messages.append('ERROR: You must run monitor mode from a TTY terminal.')
        del args.monitor

    if hasattr(args, 'monitor') and args.monitor is None:
        mode = nputop_MONITOR_MODE.intersection({'auto', 'full', 'compact'})
        mode = 'auto' if len(mode) != 1 else mode.pop()
        args.monitor = mode

    if not setlocale_utf8():
        args.ascii = True

    try:
        device_count = Device.count()
    except libnvml.NVMLError_LibraryNotFound:
        return 1
    except libnvml.NVMLError as ex:
        print(
            '{} {}'.format(colored('NVML ERROR:', color='red', attrs=('bold',)), ex),
            file=sys.stderr,
        )
        return 1

    if args.npu_util_thresh is not None:
        Device.NPU_UTILIZATION_THRESHOLDS = tuple(sorted(args.npu_util_thresh))
    if args.mem_util_thresh is not None:
        Device.MEMORY_UTILIZATION_THRESHOLDS = tuple(sorted(args.mem_util_thresh))

    if args.only is not None:
        indices = set(args.only)
        invalid_indices = indices.difference(range(device_count))
        indices.intersection_update(range(device_count))
        if len(invalid_indices) > 1:
            messages.append(f'ERROR: Invalid device indices: {sorted(invalid_indices)}.')
        elif len(invalid_indices) == 1:
            messages.append(f'ERROR: Invalid device index: {next(iter(invalid_indices))}.')
    elif args.only_visible:
        indices = {
            index if isinstance(index, int) else index[0]
            for index in Device.parse_cuda_visible_devices()
        }
    else:
        indices = set(range(device_count))
    devices = Device.from_indices(sorted(indices))

    filters = []
    if args.compute:
        filters.append(lambda process: 'C' in process.type or 'X' in process.type)
    if args.only_compute:
        filters.append(lambda process: 'G' not in process.type and 'X' not in process.type)
    if args.graphics:
        filters.append(lambda process: 'G' in process.type or 'X' in process.type)
    if args.only_graphics:
        filters.append(lambda process: 'C' not in process.type and 'X' not in process.type)
    if args.user is not None:
        users = set(args.user)
        filters.append(lambda process: process.username in users)
    if args.pid is not None:
        pids = set(args.pid)
        filters.append(lambda process: process.pid in pids)

    ui = None
    if hasattr(args, 'monitor') and len(devices) > 0:
        try:
            with libcurses(colorful=args.colorful, light_theme=args.light) as win:
                ui = UI(
                    devices,
                    filters,
                    ascii=args.ascii,
                    mode=args.monitor,
                    interval=args.interval,
                    win=win,
                )
                ui.loop()
        except curses.error as ex:
            if ui is not None:
                raise
            messages.append(f'ERROR: Failed to initialize `curses` ({ex})')

    if ui is None:
        ui = UI(devices, filters, ascii=args.ascii)
        if not sys.stdout.isatty():
            parent = HostProcess().parent()
            if parent is not None:
                grandparent = parent.parent()
                if (
                    grandparent is not None
                    and parent.name() == 'sh'
                    and grandparent.name() == 'watch'
                ):
                    messages.append(
                        'HINT: You are running `nputop` under `watch` command. '
                        'Please try `nputop -m` directly.',
                    )

    ui.print()
    ui.destroy()

    if len(messages) > 0:
        for message in messages:
            for prefix, color in (('ERROR:', 'red'), ('WARNING:', 'yellow'), ('HINT:', 'green')):
                if message.startswith(prefix):
                    message = message.replace(
                        prefix,
                        colored(prefix, color=color, attrs=('bold',)),
                        1,
                    )
                    break
            print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
