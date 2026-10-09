"""Verify release metadata, package completeness and public-only archive contents."""

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile


PUBLIC_DOCS = {
    'docs/compatibility.md',
    'docs/development.md',
    'docs/power.md',
    'docs/power_zh.md',
    'docs/publishing.md',
}
PUBLIC_TOOLS = {
    'tools/acceptance.py',
    'tools/check_install.py',
    'tools/check_distribution.py',
    'tools/prepare_conda_recipe.py',
}
PUBLIC_ROOT = {
    'README.md',
    'README_zh.md',
    'CHANGELOG.md',
    'LICENSE',
    'COPYING',
    'NOTICE',
    'pyproject.toml',
    'setup.py',
    'setup.cfg',
    'MANIFEST.in',
    'PKG-INFO',
}
EGG_INFO = {
    'PKG-INFO',
    'SOURCES.txt',
    'dependency_links.txt',
    'entry_points.txt',
    'requires.txt',
    'top_level.txt',
}


def public_source_file(name):
    """New documentation or maintainer tools require an explicit publication decision."""
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return False
    if name in PUBLIC_ROOT | PUBLIC_DOCS | PUBLIC_TOOLS | {'assets/nputop-910c.png'}:
        return True
    if parts[0] == 'nputop':
        return name.endswith('.py') or name in ('nputop/api/LICENSE', 'nputop/gui/COPYING')
    if len(parts) == 2 and parts[0] == 'tests':
        return parts[1] == 'conftest.py' or (
            parts[1].startswith('test_') and parts[1].endswith('.py')
        )
    return len(parts) == 2 and parts[0] == 'ascend_nputop.egg-info' and parts[1] in EGG_INFO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('sdist', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(str(args.wheel)) as archive:
        names = set(archive.namelist())
        metadata_name = next(n for n in names if n.endswith('.dist-info/METADATA'))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        version = metadata['Version']
        assert metadata['Name'] == 'ascend-nputop'
        assert metadata['Requires-Python'].replace(' ', '') == '>=3.7'
        assert 'preview' not in version and '+' not in version
        info = metadata_name.rsplit('/', 1)[0]
        for name in names:
            if name.endswith('/'):
                continue
            assert (name.startswith('nputop/') and public_source_file(name)) or name in {
                info + '/' + entry
                for entry in (
                    'METADATA',
                    'WHEEL',
                    'RECORD',
                    'entry_points.txt',
                    'top_level.txt',
                    'LICENSE',
                    'COPYING',
                    'NOTICE',
                    'licenses/LICENSE',
                    'licenses/COPYING',
                    'licenses/NOTICE',
                )
            }, (
                'Unexpected wheel artifact: ' + name
            )
        for path in (root / 'nputop').rglob('*.py'):
            name = path.relative_to(root).as_posix()
            assert name in names, 'Missing package module: ' + name
            assert archive.read(name) == path.read_bytes(), 'Outdated module: ' + name
        for name in ('LICENSE', 'COPYING', 'NOTICE'):
            assert name in metadata.get_all('License-File', [])
            assert any(n.startswith(info + '/') and n.rsplit('/', 1)[-1] == name for n in names), (
                'Missing distribution license: ' + name
            )
        entries = archive.read(metadata_name.replace('METADATA', 'entry_points.txt')).decode()
        assert 'nputop = nputop.cli:main' in entries and 'nvisel' not in entries
        for requirement in metadata.get_all('Requires-Dist', []):
            if requirement.lower().startswith('nvidia-ml-py'):
                assert 'extra' in requirement, 'NVML must not be a default dependency'
    with tarfile.open(str(args.sdist)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            name = member.name.partition('/')[2]
            assert member.isfile() and public_source_file(name), (
                'Unexpected source artifact: ' + member.name
            )
            if name.startswith('nputop/') and name.endswith('.py'):
                assert archive.extractfile(member).read() == (root / name).read_bytes(), (
                    'Outdated source module: ' + name
                )
        names = {n.partition('/')[2] for n in archive.getnames()}
        for name in PUBLIC_DOCS | PUBLIC_TOOLS | {'assets/nputop-910c.png'}:
            assert name in names, 'Missing public artifact: ' + name
        for name in (
            'README.md',
            'README_zh.md',
            'LICENSE',
            'COPYING',
            'NOTICE',
            'CHANGELOG.md',
            'pyproject.toml',
            'setup.py',
            'MANIFEST.in',
            'tools/acceptance.py',
            'tools/check_install.py',
            'tools/check_distribution.py',
            'tests/test_libascend.py',
            'tests/test_release_entry.py',
            'docs/compatibility.md',
        ):
            assert name in names, 'Missing source artifact: ' + name
    print(
        json.dumps(
            dict(
                version=version,
                passed=True,
                sha256={
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (args.wheel, args.sdist)
                },
            ),
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
