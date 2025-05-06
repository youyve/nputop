"""An interactive Ascend-NPU process viewer and beyond, the one-stop solution for NPU process management."""

# pylint: disable=invalid-name

__version__ = '1.0.0'
__license__ = 'GPL-3.0-only AND Apache-2.0'
__author__ = __maintainer__ = 'Lianzhong You'
__email__ = 'youlianzhong@gml.ac.cn'
__release__ = False

if not __release__:
    import os
    import subprocess

    try:
        prefix, sep, suffix = (
            subprocess.check_output(  # noqa: S603
                ['git', 'describe', '--abbrev=7'],  # noqa: S607
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
            del version_prefix, dot, version_tail
        else:
            __version__ = prefix
        del prefix, sep, suffix
    except (OSError, subprocess.CalledProcessError):
        pass

    del os, subprocess

# The package `nvidia-ml-py` is not backward compatible over releases. This may
# cause problems with Old versions of NVIDIA drivers.
# The ideal solution is to let the user install the best-fit version of `nvidia-ml-py`.
PYNVML_VERSION_CANDIDATES = (
    # Sync with pyproject.toml and requirements.txt
    '11.450.51',  # the last version supports the R430 driver (CUDA 10.x)
    '11.450.129',  # requires at last the R450 driver
    '11.460.79',
    '11.470.66',
    '11.495.46',
    '11.510.69',  # the first version supports the `nvmlMemory_v2` API
    '11.515.48',
    '11.515.75',
    '11.525.84',
    '11.525.112',
    '11.525.131',
    '11.525.150',
    '12.535.77',
    '12.535.108',
    '12.535.133',
    '12.535.161',
    '12.550.52',
    '12.550.89',
    '12.555.43',
    '12.560.30',
)
"""The list of supported ``nvidia-ml-py`` versions.
See also: `nvidia-ml-py's Release History <https://pypi.org/project/nvidia-ml-py/#history>`_.

To install ``nputop`` with a specific version of ``nvidia-ml-py``, use ``nputop[pynvml-xx.yyy.zzz]``, for example:

.. code:: bash

    pip3 install 'nputop[pynvml-11.450.51]'

or

.. code:: bash

    pip3 install nputop nvidia-ml-py==11.450.51

Note:
    The package ``nvidia-ml-py`` is not backward compatible over releases. This may cause problems
    such as *"Function Not Found"* errors with old versions of NVIDIA drivers (e.g. the NVIDIA R430
    driver on Ubuntu 16.04 LTS).
    The ideal solution is to let the user install the best-fit version of ``nvidia-ml-py``.
    See also: `nvidia-ml-py's Release History <https://pypi.org/project/nvidia-ml-py/#history>`_.

    ``nvidia-ml-py==11.450.51`` is the last version supports the NVIDIA R430 driver (CUDA 10.x).
    Since ``nvidia-ml-py>=11.450.129``, the definition of struct ``nvmlProcessInfo_t`` has introduced
    two new fields ``npuInstanceId`` and ``computeInstanceId`` (GI ID and CI ID in newer ``nvidia-smi``)
    which are incompatible with some old NVIDIA drivers. ``nputop`` may not display the processes
    correctly due to this incompatibility.
"""
