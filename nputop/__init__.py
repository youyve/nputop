"""An interactive Ascend-NPU process viewer and beyond, the one-stop solution for NPU process management."""

import sys

from nputop import api
from nputop.api import *  # noqa: F403
from nputop.api import collector, device, host, libnvml, process, utils
from nputop.select import select_devices
from nputop.version import __version__


__all__ = [*api.__all__, 'select_devices']

# Add submodules to the top-level namespace
for submodule in (collector, device, host, libnvml, process, utils):
    sys.modules[f'{__name__}.{submodule.__name__.rpartition(".")[-1]}'] = submodule

# Remove the nputop.select module from sys.modules
# Required for `python -m nputop.select` to work properly
sys.modules.pop(f'{__name__}.select', None)

del sys
