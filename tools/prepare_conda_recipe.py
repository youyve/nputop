"""Prepare the conda-forge recipe from a verified sdist; never publish it."""

import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sdist', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tarfile.open(str(args.sdist)) as archive:
        metadata_files = [
            n for n in archive.getnames() if n.count('/') == 1 and n.endswith('/PKG-INFO')
        ]
        if len(metadata_files) != 1:
            parser.error('Expected one top-level PKG-INFO in the source archive')
        metadata = BytesParser().parsebytes(archive.extractfile(metadata_files[0]).read())
    if metadata['Name'] != 'ascend-nputop':
        parser.error('Expected the ascend-nputop source distribution')
    version = metadata['Version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        parser.error('Expected a final major.minor.patch version')
    expected_names = (
        'ascend_nputop-' + version + '.tar.gz',
        'ascend-nputop-' + version + '.tar.gz',
    )
    if args.sdist.name not in expected_names:
        parser.error('Archive filename does not match its package/version metadata')
    python_requirement = metadata['Requires-Python'].replace(' ', '')
    if python_requirement != '>=3.7':
        parser.error('Review the recipe before changing the Python compatibility floor')
    requirements = []
    for requirement in metadata.get_all('Requires-Dist', []):
        if ';' in requirement:
            marker = requirement.partition(';')[2].strip()
            if re.fullmatch(
                r'''(?:extra\s*==\s*['"][\w-]+['"]|platform_system\s*==\s*['"]Windows['"])''',
                marker,
            ):
                continue  # Windows-only dependencies and optional extras.
            parser.error('Review conditional runtime dependency: ' + requirement)
        match = re.fullmatch(r'(psutil|cachetools|termcolor)\s*>=\s*([\d.]+)', requirement)
        if match is None:
            parser.error('Review new runtime dependency: ' + requirement)
        requirements.append('    - ' + match.group(1) + ' >=' + match.group(2))
    if len(requirements) != 3:
        parser.error('Expected the three existing runtime dependencies')
    digest = hashlib.sha256(args.sdist.read_bytes()).hexdigest()
    recipe = '''# Generated from the release sdist; submit to conda-forge/nputop-feedstock.
package:
  name: nputop
  version: "{version}"

source:
  url: https://pypi.org/packages/source/a/ascend-nputop/{filename}
  sha256: {digest}

build:
  number: 0
  skip: true  # [win or osx]
  script: {{{{ PYTHON }}}} -m pip install . -vv --no-build-isolation --no-deps
  entry_points:
    - nputop = nputop.cli:main

requirements:
  host:
    - python {python_requirement}
    - pip
    - setuptools >=61
    - wheel
  run:
    - python {python_requirement}
{requirements}

test:
  imports:
    - nputop
    - nputop.api.libdcmi
    - nputop.gui.monitor
  commands:
    - nputop --version
    - nputop --help
    - nputop --preview --version
    - nputop --legacy-ui --help
    - python -c "from nputop.version import __version__; assert __version__ == '{version}'"

about:
  home: https://github.com/youyve/nputop
  license: Apache-2.0 AND GPL-3.0-only
  license_file:
    - LICENSE
    - COPYING
    - NOTICE
  summary: An interactive Ascend NPU and process monitor
  description: |
    Monitor Ascend devices and processes through DCMI or npu-smi.
    Live telemetry requires Linux and an installed Ascend driver.
  doc_url: https://github.com/youyve/nputop
  dev_url: https://github.com/youyve/nputop

extra:
  recipe-maintainers:
    - youyve
'''.format(
        version=version,
        filename=args.sdist.name,
        digest=digest,
        python_requirement=python_requirement,
        requirements='\n'.join(requirements),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(recipe, encoding='utf-8')
    print('Prepared ' + str(args.output) + ' from ' + args.sdist.name)


if __name__ == '__main__':
    main()
