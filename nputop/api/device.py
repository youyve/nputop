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


class UtilizationTelemetry(NamedTuple):
    """Raw device-wide utilization values used to derive snapshot fields."""

    npu: int | NaType
    memory: int | NaType
    bandwidth: int | NaType
    aicpu: int | NaType


class ClockInfos(NamedTuple):
    graphics: int | NaType
    sm: int | NaType
    memory: int | NaType
    video: int | NaType


class ClockSpeedInfos(NamedTuple):
    current: ClockInfos
    max: ClockInfos


class ThroughputInfo(NamedTuple):
    tx: int | NaType
    rx: int | NaType

    @property
    def transmit(self) -> int | NaType:
        return self.tx

    @property
    def receive(self) -> int | NaType:
        return self.rx


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
            value = libnvml.nvmlQuery(
                "ascendDeviceGetUUID", self.index, default=NA
            )
            # DCMI exposes stable card/chip identity through the compatibility
            # layer. Keep the old index-based value when running the fallback
            # parser or when a driver does not provide one.
            self._uuid = value if isinstance(value, str) and value != NA else f"ASCEND-{self.index:02d}"
        return self._uuid

    def bus_id(self) -> str | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetBusId", self.index, default=NA)

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
    # Fan telemetry (DCMI reports RPM, compatibility layer normalizes it to %)
    # ------------------------------------------------------------
    def fan_speed(self) -> int | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetFanSpeed", self.index)

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
        li = f"{float(li)}W" if isinstance(li, int) else "N/A"
        return f"{pu/1000 if isinstance(pu, (int,float)) else pu}W / {li}"

    # ------------------------------------------------------------
    # 内存
    # ------------------------------------------------------------
    @staticmethod
    def _normalize_memory_info(info: Any) -> MemoryInfo:
        if isinstance(info, (tuple, list)) and len(info) >= 3:
            total, free, used = info[:3]
        else:
            total = getattr(info, "total", NA)
            free = getattr(info, "free", NA)
            used = getattr(info, "used", NA)

        if not all(isinstance(value, int) for value in (total, free, used)):
            return MemoryInfo(total=NA, free=NA, used=NA)

        return MemoryInfo(total=total, free=free, used=used)

    @memoize_when_activated
    def memory_info(self) -> MemoryInfo:
        # DCMI obtains memory usage from the HBM query. Reuse its result in a
        # snapshot so the memory fields and HBM telemetry do not each make a
        # blocking driver call. Parser-based backends do not expose HBM
        # telemetry, in which case keep the existing memory-info fallback.
        hbm = self.hbm_info()
        total = getattr(hbm, "total", NA)
        used = getattr(hbm, "used", NA)
        if isinstance(total, int) and isinstance(used, int):
            return MemoryInfo(total=total, free=max(total - used, 0), used=used)

        info = libnvml.nvmlQuery(
            "ascendDeviceGetMemoryInfo",
            self.index,
            default=MemoryInfo(total=NA, free=NA, used=NA),
        )
        return self._normalize_memory_info(info)

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
        if isinstance(info.total, int) and isinstance(info.used, int) and info.total:
            return round(100.0 * info.used / info.total, 1)
        return NA

    def memory_usage(self) -> str:
        return f"{self.memory_used_human()} / {self.memory_total_human()}"

    # ------------------------------------------------------------
    # 利用率
    # ------------------------------------------------------------
    @memoize_when_activated
    def _utilization_telemetry(self) -> UtilizationTelemetry:
        """Collect the DCMI utilization group once per ``oneshot`` frame."""

        util = libnvml.nvmlQuery("ascendDeviceGetUtilizationRates", self.index)
        if not isinstance(util, (tuple, list)) or len(util) < 2:
            return UtilizationTelemetry(NA, NA, NA, NA)
        return UtilizationTelemetry(
            npu=util[0],
            memory=util[1],
            bandwidth=getattr(util, "bandwidth", NA),
            aicpu=getattr(util, "aicpu", NA),
        )

    @memoize_when_activated
    def utilization_rates(self) -> UtilizationRates:
        telemetry = self._utilization_telemetry()
        if telemetry.npu != NA and telemetry.memory != NA:
            dvpp = libnvml.nvmlQuery(
                "ascendDeviceGetDvppUtilization", self.index, default=(NA, NA)
            )
            decoder = dvpp[0] if isinstance(dvpp, (tuple, list)) else NA
            encoder = dvpp[1] if isinstance(dvpp, (tuple, list)) else NA
            return UtilizationRates(
                npu=telemetry.npu,
                memory=telemetry.memory,
                encoder=encoder,
                decoder=decoder,
            )
        return UtilizationRates(npu=NA, memory=NA, encoder=NA, decoder=NA)

    def npu_utilization(self) -> int | NaType:
        """Return overall NPU utilization for DCMI, or AICore for the fallback.

        The optional DCMI backend reports a device-wide NPU value; the legacy
        ``npu-smi`` parser exposes its AICore utilization instead.
        """
        return self.utilization_rates().npu

    gpu_utilization = npu_utilization

    def memory_utilization(self) -> int | NaType:
        return self.utilization_rates().memory

    def memory_bandwidth_utilization(self) -> int | NaType:
        return self._utilization_telemetry().bandwidth

    def aicpu_utilization(self) -> int | NaType:
        return self._utilization_telemetry().aicpu

    @memoize_when_activated
    def hbm_info(self) -> Any:
        return libnvml.nvmlQuery("ascendDeviceGetHbmInfo", self.index)

    def hbm_frequency(self) -> int | NaType:
        return getattr(self.hbm_info(), "frequency", NA)

    def hbm_temperature(self) -> int | NaType:
        return getattr(self.hbm_info(), "temperature", NA)

    def hbm_bandwidth_utilization(self) -> int | NaType:
        return getattr(self.hbm_info(), "bandwidth", NA)

    # Backwards-compatible alias for the original ambiguous name.
    hbm_bandwidth = hbm_bandwidth_utilization

    def decoder_utilization(self) -> int | NaType:
        return self.utilization_rates().decoder

    def encoder_utilization(self) -> int | NaType:
        return self.utilization_rates().encoder

    @memoize_when_activated
    def clock_infos(self) -> ClockInfos:
        info = libnvml.nvmlQuery(
            "ascendDeviceGetClockInfos",
            self.index,
            default=ClockInfos(NA, NA, NA, NA),
        )
        if isinstance(info, (tuple, list)) and len(info) >= 4:
            return ClockInfos(*info[:4])
        return ClockInfos(NA, NA, NA, NA)

    clocks = clock_infos

    @memoize_when_activated
    def max_clock_infos(self) -> ClockInfos:
        info = libnvml.nvmlQuery(
            "ascendDeviceGetMaxClockInfos",
            self.index,
            default=ClockInfos(NA, NA, NA, NA),
        )
        if isinstance(info, (tuple, list)) and len(info) >= 4:
            return ClockInfos(*info[:4])
        return ClockInfos(NA, NA, NA, NA)

    max_clocks = max_clock_infos

    def clock_speed_infos(self) -> ClockSpeedInfos:
        return ClockSpeedInfos(self.clock_infos(), self.max_clock_infos())

    def aicore_clock(self) -> int | NaType:
        return self.clock_infos().sm

    # ``sm_clock`` is inherited from nvitop's CUDA-shaped API. Preserve it
    # for callers while exposing the Ascend-native meaning explicitly.
    sm_clock = aicore_clock

    def max_aicore_clock(self) -> int | NaType:
        return self.max_clock_infos().sm

    max_sm_clock = max_aicore_clock

    def memory_clock(self) -> int | NaType:
        return self.clock_infos().memory

    def video_clock(self) -> int | NaType:
        return self.clock_infos().video

    @memoize_when_activated
    def pcie_throughput(self) -> ThroughputInfo:
        info = libnvml.nvmlQuery(
            "ascendDeviceGetPcieThroughput",
            self.index,
            default=ThroughputInfo(NA, NA),
        )
        if isinstance(info, (tuple, list)) and len(info) >= 2:
            return ThroughputInfo(*info[:2])
        return ThroughputInfo(NA, NA)

    def pcie_tx_throughput(self) -> int | NaType:
        return self.pcie_throughput().tx

    def pcie_rx_throughput(self) -> int | NaType:
        return self.pcie_throughput().rx

    def pcie_tx_throughput_human(self) -> str | NaType:
        value = self.pcie_tx_throughput()
        return f"{bytes2human(value * 1024)}/s" if isinstance(value, int) else NA

    def pcie_rx_throughput_human(self) -> str | NaType:
        value = self.pcie_rx_throughput()
        return f"{bytes2human(value * 1024)}/s" if isinstance(value, int) else NA

    def total_volatile_uncorrected_ecc_errors(self) -> int | NaType:
        return libnvml.nvmlQuery("ascendDeviceGetEccErrors", self.index)

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
                    self._utilization_telemetry.cache_activate(self)  # type: ignore[attr-defined]
                    self.hbm_info.cache_activate(self)            # type: ignore[attr-defined]
                    self.clock_infos.cache_activate(self)        # type: ignore[attr-defined]
                    self.max_clock_infos.cache_activate(self)    # type: ignore[attr-defined]
                    self.pcie_throughput.cache_activate(self)    # type: ignore[attr-defined]
                    yield
                finally:
                    self.memory_info.cache_deactivate(self)      # type: ignore[attr-defined]
                    self.utilization_rates.cache_deactivate(self)  # type: ignore[attr-defined]
                    self._utilization_telemetry.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.hbm_info.cache_deactivate(self)            # type: ignore[attr-defined]
                    self.clock_infos.cache_deactivate(self)      # type: ignore[attr-defined]
                    self.max_clock_infos.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.pcie_throughput.cache_deactivate(self)  # type: ignore[attr-defined]

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
        "aicore_clock", "max_aicore_clock", "sm_clock", "max_sm_clock",
        "memory_clock", "video_clock",
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
