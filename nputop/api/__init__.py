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


"""The core APIs of nputop."""

from nputop.api import collector, device, host, libnvml, process, utils
from nputop.api.collector import ResourceMetricCollector, collect_in_background, take_snapshots
from nputop.api.device import (
    CudaDevice,
    CudaMigDevice,
    Device,
    MigDevice,
    PhysicalDevice,
    normalize_cuda_visible_devices,
    parse_cuda_visible_devices,
)
from nputop.api.libnvml import NVMLError, nvmlCheckReturn
from nputop.api.process import NpuProcess, HostProcess, command_join
from nputop.api.utils import (  # explicitly export these to appease mypy
    NA,
    SIZE_UNITS,
    UINT_MAX,
    ULONGLONG_MAX,
    GiB,
    KiB,
    MiB,
    NaType,
    NotApplicable,
    NotApplicableType,
    PiB,
    Snapshot,
    TiB,
    boolify,
    bytes2human,
    colored,
    human2bytes,
    set_color,
    timedelta2human,
    utilization2string,
)


__all__ = [
    'NVMLError',
    'nvmlCheckReturn',
    'libnvml',
    # nputop.api.device
    'Device',
    'PhysicalDevice',
    'MigDevice',
    'CudaDevice',
    'CudaMigDevice',
    'parse_cuda_visible_devices',
    'normalize_cuda_visible_devices',
    # nputop.api.process
    'host',
    'HostProcess',
    'NpuProcess',
    'command_join',
    # nputop.api.collector
    'take_snapshots',
    'collect_in_background',
    'ResourceMetricCollector',
    # nputop.api.utils
    'NA',
    'NaType',
    'NotApplicable',
    'NotApplicableType',
    'UINT_MAX',
    'ULONGLONG_MAX',
    'KiB',
    'MiB',
    'GiB',
    'TiB',
    'PiB',
    'SIZE_UNITS',
    'bytes2human',
    'human2bytes',
    'timedelta2human',
    'utilization2string',
    'colored',
    'set_color',
    'boolify',
    'Snapshot',
]
