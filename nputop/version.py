"""An interactive Ascend-NPU process viewer and beyond, the one-stop solution for NPU process management."""

# pylint: disable=invalid-name

__version__ = '1.0.0'
__license__ = 'Apache-2.0 AND GPL-3.0-only'
__author__ = __maintainer__ = 'Lianzhong You'
__email__ = 'youlianzhong@gml.ac.cn'
__release__ = False

if not __release__:
    import os
    import subprocess

    try:
        # 通过 git tag 自动生成 dev 版本号
        prefix, sep, suffix = (
            subprocess.check_output(
                ['git', 'describe', '--abbrev=7'],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
            .lstrip('v')
            .replace('-', '.dev', 1)
            .replace('-', '+', 1)
            .partition('.dev')
        )
        if sep:
            version_prefix, dot, version_tail = prefix.rpartition('.')
            prefix = f'{version_prefix}{dot}{int(version_tail) + 1}'
            __version__ = f'{prefix}{sep}{suffix}'
        else:
            __version__ = prefix
    except (OSError, subprocess.CalledProcessError):
        # 如果无法调用 git，就保持手写的 __version__
        pass
    finally:
        # 清理临时导入
        try:
            del os, subprocess
        except NameError:
            pass
