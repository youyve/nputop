# SPDX-License-Identifier: Apache-2.0
"""查找当前环境使用的 CANN Toolkit 版本。"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path


_INSTALL_INFO = 'ascend_toolkit_install.info'
_DEFAULT_PREFIX = Path('/usr/local/Ascend')
_VERSION = re.compile(r'[0-9]+\.[0-9]+[A-Za-z0-9_.+-]*')


def detect_cann_version():
    """返回版本和可公开的检测信息，检测信息只记录来源名称及错误类别。"""
    machine = platform.machine().lower()
    arch = {'arm64': 'aarch64', 'amd64': 'x86_64'}.get(machine, machine)
    sources = []
    for name in ('ASCEND_HOME_PATH', 'ASCEND_TOOLKIT_HOME'):
        value = os.environ.get(name)
        if value:
            sources.append((name, Path(value)))
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        try:
            sources.append(('user-default', Path.home() / 'Ascend'))
        except RuntimeError:
            pass
    sources.append(('default', _DEFAULT_PREFIX))

    report = {'source': None, 'failures': []}
    for source, prefix in sources:
        failures = set()
        for layout in ('.', 'cann', 'latest', 'ascend-toolkit/latest'):
            root = prefix / layout
            candidates = (root / (arch + '-linux') / _INSTALL_INFO, root / _INSTALL_INFO)
            for candidate in candidates:
                try:
                    content = candidate.read_text(encoding='utf-8-sig')
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except PermissionError:
                    failures.add('permission')
                    continue
                except UnicodeError:
                    failures.add('invalid-version')
                    continue
                except (OSError, ValueError):
                    failures.add('unreadable')
                    continue

                versions = []
                for line in content.splitlines():
                    key, sep, value = line.partition('=')
                    if sep and key.strip() == 'version':
                        versions.append(value.strip())
                if len(versions) == 1 and _VERSION.fullmatch(versions[0]):
                    report['source'] = source
                    return versions[0], report
                failures.add('invalid-version')
        report['failures'].append({'source': source, 'reasons': sorted(failures or {'not-found'})})

    return None, report


def cann_version():
    """返回 CANN Toolkit 版本，找不到有效安装信息时返回 None。"""
    return detect_cann_version()[0]
