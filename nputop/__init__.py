# This file is part of nputop, the interactive Ascend-NPU process viewer.
#
# Copyright (c) 2025 Xuehai Pan <XuehaiPan@pku.edu.cn>
# Copyright (c) 2025 Lianzhong You <youlianzhong@gml.ac.cn>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""An interactive Ascend-NPU process viewer and beyond, the one-stop solution for NPU process management."""

import sys

from nputop import api
from nputop.api import *  # noqa: F403
from nputop.api import collector, device, host, process, utils
from nputop.select import select_devices
from nputop.version import __version__


__all__ = [*api.__all__, 'select_devices']

# Add submodules to the top-level namespace
for submodule in (collector, device, host, process, utils):
    sys.modules[f'{__name__}.{submodule.__name__.rpartition(".")[-1]}'] = submodule

# Remove the nputop.select module from sys.modules
# Required for `python -m nputop.select` to work properly
sys.modules.pop(f'{__name__}.select', None)

del sys
