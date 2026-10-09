"""Check an installed release outside its source tree; never signal workloads."""

import argparse
from email.parser import Parser
import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-root', type=Path, required=True)
    parser.add_argument('--console', type=Path, required=True)
    parser.add_argument('--hardware', action='store_true')
    parser.add_argument('--no-driver', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.hardware and args.no_driver:
        parser.error('--hardware and --no-driver are mutually exclusive')

    import nputop
    from nputop.version import __version__, __preview_version__

    # Include legacy subpackages: incomplete wheels used to omit these.
    for name in (
        'nputop.gui.screens.main.device',
        'nputop.gui.screens.main.process',
        'nputop.gui.library.widestring',
        'nputop.api.libascend',
        'nputop.api.libdcmi',
    ):
        importlib.import_module(name)

    location = Path(nputop.__file__).resolve()
    location.relative_to(args.expected_root.resolve())
    assert __version__ == __preview_version__ and 'preview' not in __version__
    metadata = list(location.parent.parent.glob('ascend_nputop-*.dist-info/METADATA'))
    assert len(metadata) == 1, 'Missing or ambiguous installed distribution metadata'
    installed = Parser().parsestr(metadata[0].read_text(encoding='utf-8'))
    assert installed['Version'] == __version__
    assert installed['Requires-Python'].replace(' ', '') == '>=3.7'
    report = dict(version=__version__, python=platform.python_version(), commands=[])

    def invoke(command, flags, expected=0):
        completed = subprocess.run(
            command + flags,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        )
        # Do not include captured process output in reports, including failures.
        assert completed.returncode == expected, (flags, completed.returncode)
        assert 'Traceback' not in completed.stderr, flags
        report['commands'].append(dict(flags=flags, exit_code=completed.returncode))
        return completed

    module = [sys.executable, '-m', 'nputop']
    for entry in (module, [str(args.console.resolve())]):
        for mode in ([], ['--preview'], ['--legacy-ui']):
            result = invoke(entry, mode + ['--version'])
            assert result.stdout.strip() == 'nputop ' + __version__
            result = invoke(entry, mode + ['--help'])
            assert 'Ascend' in result.stdout
            if '--legacy-ui' not in mode:
                assert '--backend' in result.stdout and '--legacy-ui' in result.stdout
    invoke(module, ['--interval', 'nan'], expected=2)
    invoke(module, ['--compute'], expected=2)
    invoke(module, ['--only', '-1'], expected=2)
    if args.no_driver:
        for mode in ([], ['--preview'], ['--legacy-ui']):
            result = invoke(module, mode + ['--once'], expected=1)
            assert result.stderr.strip(), mode
    if args.hardware:
        report['hardware'] = {}
        for backend in ('auto', 'dcmi', 'smi'):
            # Default auto invocation specifically exercises the new default entry.
            flags = ([] if backend == 'auto' else ['--backend', backend]) + ['--json']
            frame = json.loads(invoke(module, flags).stdout)
            assert frame['devices'], backend
            if backend != 'auto':
                assert frame['backend'] == backend
            report['hardware'][backend] = dict(
                backend=frame['backend'],
                cards=len({d['card'] for d in frame['devices']}),
                chips=len(frame['devices']),
                collection_ms=frame['collection_ms'],
            )
        invalid = str(max(d['id'] for d in frame['devices']) + 1000)
        invoke(module, ['--only', invalid, '--json'], expected=2)
        invoke(module, ['--legacy-ui', '--once'])
    report['passed'] = True
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n')
    print(rendered)


if __name__ == '__main__':
    main()
