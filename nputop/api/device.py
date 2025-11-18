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


# nputop/api/device.py
# ===============================================================
# Ascend NPU 兼容版 —— 去掉 NVLink / Fan / Display 等显卡专属逻辑，
# 用 libascend (pyACL + npu-smi) 提供的信息替代。
# ===============================================================
from __future__ import annotations

import contextlib
import os
import threading
from typing import Any, Generator, Iterable, NamedTuple

from nputop.api import libascend as libnvml
from nputop.api.process import NpuProcess
from nputop.api.utils import (
    NA,
    NaType,
    Snapshot,
    bytes2human,
    memoize_when_activated,
)


# ────────────────────────────────────────────────────────────────
# NamedTuple 定义
# ────────────────────────────────────────────────────────────────
class MemoryInfo(NamedTuple):
    total: int | NaType
    free: int | NaType
    used: int | NaType


class UtilizationRates(NamedTuple):
    npu: int | NaType
    memory: int | NaType
    encoder: int | NaType
    decoder: int | NaType

    @property
    def gpu(self) -> int | NaType:  # NVML 兼容别名
        return self.npu


# ────────────────────────────────────────────────────────────────
# Ascend NPU 设备类
# ────────────────────────────────────────────────────────────────
class Device:  # pylint: disable=too-many-instance-attributes
    NPU_PROCESS_CLASS = NpuProcess

    def __init__(self, index: int):
        self._index = index
        self._lock: threading.RLock = threading.RLock()
        self._name: str | None = None
        self._uuid: str | None = None
        self._memory_total_human: str | NaType = NA

    # ------------------------------------------------------------
    # 列表/构造
    # ------------------------------------------------------------
    @classmethod
    def from_indices(
        cls,
        indices: int | Iterable[int] | None = None,
    ) -> list[Device]:
        if indices is None:
            indices = range(cls.count())
        elif isinstance(indices, int):
            indices = [indices]
        devices: list[Device] = []
        for idx in indices:  # type: ignore[iteration]
            try:
                devices.append(cls(idx))  # type: ignore[arg-type]
            except Exception:
                continue
        return devices

    # ------------------------------------------------------------
    # 标识
    # ------------------------------------------------------------
    @property
    def index(self) -> int:
        return self._index

    @property
    def physical_index(self) -> int:
        return self._index

    def name(self) -> str | NaType:
        if self._name is None:
            self._name = libnvml.nvmlQuery("ascendDeviceGetName", self.index)
        return self._name or NA

    def uuid(self) -> str | NaType:
        if self._uuid is None:
            # Ascend 没 UUID，用伪造方便去重
            self._uuid = f"ASCEND-{self.index:02d}"
        return self._uuid

    def bus_id(self) -> str | NaType:
        return NA

    # ------------------------------------------------------------
    # 设备数量
    # ------------------------------------------------------------
    @classmethod
    def count(cls) -> int:
        return libnvml.nvmlQuery("ascendDeviceGetCount", default=0)

    # ------------------------------------------------------------
    # 驱动 / CUDA 兼容
    # ------------------------------------------------------------
    @staticmethod
    def driver_version() -> str | NaType:
        return libnvml.nvmlQuery("ascendSystemGetDriverVersion", default=NA)

    @staticmethod
    def cuda_driver_version() -> str | NaType:
        return libnvml.nvmlQuery("ascendSystemGetCANNVersion", default=NA)
    
    max_cuda_version = driver_version

    # ------------------------------------------------------------
    # Display / Persistence / Compute / Performance 占位
    # ------------------------------------------------------------
    def display_active(self) -> str | NaType:
        return "Disabled"

    def display_mode(self) -> str | NaType:
        return "N/A"

    def current_driver_model(self) -> str | NaType:
        return "N/A"

    driver_model = current_driver_model

    def persistence_mode(self) -> str | NaType:
        return "Disabled"

    def compute_mode(self) -> str | NaType:
        return "Default"

    def mig_mode(self) -> str | NaType:
        return "Disabled"

    def is_mig_mode_enabled(self) -> bool:
        return False

    def is_mig_device(self) -> bool:
        return False

    def performance_state(self) -> str | NaType:
        return "N/A"

    # ------------------------------------------------------------
    # Fan 占位
    # ------------------------------------------------------------
    def fan_speed(self) -> int | NaType:
        return NA

    # ------------------------------------------------------------
    # 温度 & 功耗
    # ------------------------------------------------------------
    def temperature(self) -> int | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetTemperature", self.index)

    def power_usage(self) -> int | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetPowerUsage", self.index)

    def power_limit(self) -> int | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetPowerLimit", self.index)

    def power_status(self) -> str | NaType:
        pu = self.power_usage()
        li = self.power_limit()
        return f"{pu/1000 if isinstance(pu, (int,float)) else pu}W / {float(li) if isinstance(li, int) else "N/A"}W"

    # ------------------------------------------------------------
    # 内存
    # ------------------------------------------------------------
    @memoize_when_activated
    def memory_info(self) -> MemoryInfo:
        return libnvml.nvmlQuery("ascendDeviceGetMemoryInfo", self.index)

    def memory_total(self) -> int | NaType:
        return self.memory_info().total

    def memory_used(self) -> int | NaType:
        return self.memory_info().used

    def memory_free(self) -> int | NaType:
        return self.memory_info().free

    def memory_total_human(self) -> str | NaType:
        if self._memory_total_human == NA:
            self._memory_total_human = bytes2human(self.memory_total())
        return self._memory_total_human

    def memory_used_human(self) -> str | NaType:
        return bytes2human(self.memory_used())

    def memory_free_human(self) -> str | NaType:
        return bytes2human(self.memory_free())

    def memory_percent(self) -> float | NaType:
        info = self.memory_info()
        if isinstance(info.total, int) and info.total:
            return round(100.0 * info.used / info.total, 1)
        return NA

    def memory_usage(self) -> str:
        return f"{self.memory_used_human()} / {self.memory_total_human()}"

    # ------------------------------------------------------------
    # 利用率
    # ------------------------------------------------------------
    @memoize_when_activated
    def utilization_rates(self) -> UtilizationRates:
        util = libnvml.nvmlQuery("ascendDeviceGetUtilizationRates", self.index)
        if isinstance(util, (tuple, list)) and len(util) >= 2:
            return UtilizationRates(npu=util[0], memory=util[1], encoder=NA, decoder=NA)
        return UtilizationRates(npu=NA, memory=NA, encoder=NA, decoder=NA)

    def npu_utilization(self) -> int | NaType:
        return self.utilization_rates().npu

    gpu_utilization = npu_utilization

    def memory_utilization(self) -> int | NaType:
        return self.utilization_rates().memory

    def encoder_utilization(self) -> int | NaType:
        return NA

    def decoder_utilization(self) -> int | NaType:
        return NA

    # ------------------------------------------------------------
    # 进程列表
    # ------------------------------------------------------------
    def processes(self) -> dict[int, NpuProcess]:
        procs: dict[int, NpuProcess] = {}
        for p in libnvml.nvmlQuery("ascendDeviceGetProcessInfo", self.index, default=()):
            proc = self.NPU_PROCESS_CLASS(pid=p.pid, device=self, npu_memory=p.usedNpuMemory)
            proc.set_npu_utilization(NA, NA, NA, NA)
            procs[p.pid] = proc
        return procs

    # ------------------------------------------------------------
    # oneshot 缓存
    # ------------------------------------------------------------
    @contextlib.contextmanager
    def oneshot(self) -> Generator[None, None, None]:
        with self._lock:
            if hasattr(self, "_cache"):
                yield
            else:
                try:
                    self.memory_info.cache_activate(self)        # type: ignore[attr-defined]
                    self.utilization_rates.cache_activate(self)  # type: ignore[attr-defined]
                    yield
                finally:
                    self.memory_info.cache_deactivate(self)      # type: ignore[attr-defined]
                    self.utilization_rates.cache_deactivate(self)  # type: ignore[attr-defined]

    # ------------------------------------------------------------
    # 快照字段：与原 NVML 版保持一致
    # ------------------------------------------------------------
    SNAPSHOT_KEYS = [
        "name", "uuid", "bus_id",
        "memory_info",
        "memory_used", "memory_free", "memory_total",
        "memory_used_human", "memory_free_human", "memory_total_human",
        "memory_percent", "memory_usage",
        "utilization_rates",
        "npu_utilization", "memory_utilization",
        "encoder_utilization", "decoder_utilization",
        "clock_infos", "max_clock_infos", "clock_speed_infos",
        "sm_clock", "memory_clock", "video_clock",
        "fan_speed", "temperature",
        "power_usage", "power_limit", "power_status",
        "pcie_throughput", "pcie_tx_throughput", "pcie_rx_throughput",
        "pcie_tx_throughput_human", "pcie_rx_throughput_human",
        "display_active", "display_mode", "current_driver_model",
        "persistence_mode", "performance_state",
        "total_volatile_uncorrected_ecc_errors",
        "compute_mode", "cuda_compute_capability",
    ]

    def as_snapshot(self) -> Snapshot:
        with self.oneshot():
            data = {k: getattr(self, k)() for k in self.SNAPSHOT_KEYS}
            return Snapshot(
                real=self,
                index=self.index,
                physical_index=self.physical_index,
                **data,
            )

    def __repr__(self) -> str:
        return (
            f"Device(index={self.index}, "
            f"name={self.name()!r}, "
            f"total_mem={self.memory_total_human()})"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Device) and other.index == self.index

    def __hash__(self) -> int:
        return hash((self.index, self.uuid()))


# ────────────────────────────────────────────────────────────────
# 动态打桩：Ascend 不支持的 NVML-only 接口全部返回 NA
# ────────────────────────────────────────────────────────────────
def _na_method(name: str):
    def _impl(self, *args: Any, **kwargs: Any) -> Any:
        return NA
    _impl.__name__ = name
    return _impl

for _name in Device.SNAPSHOT_KEYS:
    if not hasattr(Device, _name):
        setattr(Device, _name, _na_method(_name))

# ────────────────────────────────────────────────────────────────
# 工具 & 导出
# ────────────────────────────────────────────────────────────────
def list_devices() -> list[Device]:
    return [Device(i) for i in range(Device.count())]

def _env_visible_devices() -> str | None:
    return (
        os.getenv("ASCEND_RT_VISIBLE_DEVICES")
        or os.getenv("CUDA_VISIBLE_DEVICES")
        or None
    )

def parse_cuda_visible_devices(
    cuda_visible_devices: str | None = None,
) -> list[int]:
    if cuda_visible_devices is None:
        cuda_visible_devices = _env_visible_devices()
    if not cuda_visible_devices:
        return list(range(Device.count()))
    ids: list[int] = []
    for tok in cuda_visible_devices.split(","):
        tok = tok.strip()
        if tok.isdigit():
            ids.append(int(tok))
    return ids

def normalize_cuda_visible_devices(
    cuda_visible_devices: str | None = None,
) -> str:
    return ",".join(str(i) for i in parse_cuda_visible_devices(cuda_visible_devices))


PhysicalDevice = Device
MigDevice = Device
CudaDevice = Device  # Ascend 没 CUDA，但保留占位
CudaMigDevice = Device

__all__ = [
    "Device",
    "PhysicalDevice",
    'MigDevice',
    "CudaDevice",
    'CudaMigDevice',
    "list_devices",
    "parse_cuda_visible_devices",
    "normalize_cuda_visible_devices",
]
