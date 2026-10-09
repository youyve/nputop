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

"""Small ctypes binding for the CANN DCMI device-information API.

The public nputop API historically used an NVML-shaped compatibility layer.
This module deliberately keeps the DCMI binding separate from that layer so
that loading the optional CANN library does not affect imports on systems
without Ascend drivers.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import platform
import sys
from collections import namedtuple
from typing import Any, Iterable


NA = "N/A"
DCMI_OK = 0
MAX_CARD_NUM = 64
MAX_CHIP_NAME_LEN = 32
MAX_PROC_NUM_IN_DEVICE = 64
PCIE_PROFILING_TIME_MS = 100
DCMI_FAN_MAX_RPM = 18000

# DCMI utilization-rate selectors from dcmi_interface_api.h.
DCMI_UTILIZATION_RATE_DDR = 1
DCMI_UTILIZATION_RATE_AICPU = 3
DCMI_UTILIZATION_RATE_HBM = 6
DCMI_UTILIZATION_RATE_HBM_BANDWIDTH = 10
DCMI_UTILIZATION_RATE_NPU = 13

# DCMI frequency selectors from dcmi_interface_api.h.
DCMI_FREQ_DDR = 1
DCMI_FREQ_HBM = 6
DCMI_FREQ_AICORE_CURRENT = 7
DCMI_FREQ_AICORE_MAX = 9

# ``dcmi_get_device_ecc_info`` currently supports HBM on Ascend 910B.
DCMI_DEVICE_TYPE_HBM = 2


MemoryInfo = namedtuple("MemoryInfo", "total free used")
ProcessInfo = namedtuple("Proc", "pid usedNpuMemory")
UtilizationRates = namedtuple("UtilizationRates", "npu mem bandwidth aicpu")
HbmInfo = namedtuple("HbmInfo", "total frequency used temperature bandwidth")
ClockInfos = namedtuple("ClockInfos", "graphics sm memory video")
ClockSpeedInfos = namedtuple("ClockSpeedInfos", "current max")
ThroughputInfo = namedtuple("ThroughputInfo", "tx rx")
DvppUtilization = namedtuple("DvppUtilization", "decoder encoder")
EccInfo = namedtuple(
    "EccInfo",
    "enabled single_bit double_bit total_single_bit total_double_bit "
    "single_bit_isolated_pages double_bit_isolated_pages",
)


class DcmiUnavailable(RuntimeError):
    """Raised when DCMI cannot be initialized or enumerate devices."""


class _DeviceRef:
    __slots__ = ("index", "card_id", "chip_id")

    def __init__(self, index: int, card_id: int, chip_id: int) -> None:
        self.index = index
        self.card_id = card_id
        self.chip_id = chip_id


class _ChipInfo(ctypes.Structure):
    _fields_ = [
        ("chip_type", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("chip_name", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("chip_ver", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("aicore_cnt", ctypes.c_uint),
    ]


class _ChipInfoV2(ctypes.Structure):
    _fields_ = [
        ("chip_type", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("chip_name", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("chip_ver", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
        ("aicore_cnt", ctypes.c_uint),
        ("npu_name", ctypes.c_ubyte * MAX_CHIP_NAME_LEN),
    ]


class _PcieInfoV2(ctypes.Structure):
    _fields_ = [
        ("venderid", ctypes.c_uint),
        ("subvenderid", ctypes.c_uint),
        ("deviceid", ctypes.c_uint),
        ("subdeviceid", ctypes.c_uint),
        ("domain", ctypes.c_int),
        ("bdf_busid", ctypes.c_uint),
        ("bdf_deviceid", ctypes.c_uint),
        ("bdf_funcid", ctypes.c_uint),
        ("reserve", ctypes.c_ubyte * 32),
    ]


class _HbmInfo(ctypes.Structure):
    _fields_ = [
        ("memory_size", ctypes.c_ulonglong),
        ("freq", ctypes.c_uint),
        ("memory_usage", ctypes.c_ulonglong),
        ("temp", ctypes.c_int),
        ("bandwith_util_rate", ctypes.c_uint),
    ]


class _MemoryInfoV3(ctypes.Structure):
    _fields_ = [
        ("memory_size", ctypes.c_ulonglong),
        ("memory_available", ctypes.c_ulonglong),
        ("freq", ctypes.c_uint),
        ("hugepagesize", ctypes.c_ulong),
        ("hugepages_total", ctypes.c_ulong),
        ("hugepages_free", ctypes.c_ulong),
        ("utiliza", ctypes.c_uint),
        ("reserve", ctypes.c_ubyte * 60),
    ]


class _ProcMemInfo(ctypes.Structure):
    _fields_ = [
        ("proc_id", ctypes.c_int),
        ("proc_mem_usage", ctypes.c_ulong),
    ]


class _EccInfo(ctypes.Structure):
    _fields_ = [
        ("enable_flag", ctypes.c_int),
        ("single_bit_error_cnt", ctypes.c_uint),
        ("double_bit_error_cnt", ctypes.c_uint),
        ("total_single_bit_error_cnt", ctypes.c_uint),
        ("total_double_bit_error_cnt", ctypes.c_uint),
        ("single_bit_isolated_pages_cnt", ctypes.c_uint),
        ("double_bit_isolated_pages_cnt", ctypes.c_uint),
    ]


class _DvppRatio(ctypes.Structure):
    _fields_ = [
        ("vdec_ratio", ctypes.c_int),
        ("vpc_ratio", ctypes.c_int),
        ("venc_ratio", ctypes.c_int),
        ("jpege_ratio", ctypes.c_int),
        ("jpegd_ratio", ctypes.c_int),
    ]


class _PcieLinkBandwidthInfo(ctypes.Structure):
    _fields_ = [
        ("profiling_time", ctypes.c_int),
        ("tx_p_bw", ctypes.c_uint * 3),
        ("tx_np_bw", ctypes.c_uint * 3),
        ("tx_cpl_bw", ctypes.c_uint * 3),
        ("tx_np_lantency", ctypes.c_uint * 3),
        ("rx_p_bw", ctypes.c_uint * 3),
        ("rx_np_bw", ctypes.c_uint * 3),
        ("rx_cpl_bw", ctypes.c_uint * 3),
    ]


def _decode_char_array(value: Any) -> str:
    """Decode a DCMI fixed-size C string without leaking NUL padding."""

    if isinstance(value, str):
        return value.split("\0", 1)[0].strip()
    try:
        raw = bytes(value)
    except (TypeError, ValueError):
        return ""
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def _configure_function(function: Any, argtypes: list[Any]) -> Any:
    """Set ctypes metadata when *function* is a ctypes symbol.

    Test doubles are ordinary Python callables and do not allow arbitrary
    ``argtypes``/``restype`` attributes, so metadata assignment is best effort.
    """

    try:
        function.argtypes = argtypes
        function.restype = ctypes.c_int
    except (AttributeError, TypeError):
        pass
    return function


def _library_candidates() -> Iterable[str]:
    """Yield likely CANN driver-library locations in deterministic order."""

    candidates: list[str] = []
    for variable in ("ASCEND_DRIVER_HOME", "ASCEND_INSTALL_PATH", "ASCEND_HOME_PATH"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(
                [
                    os.path.join(root, "lib64", "driver", "libdcmi.so"),
                    os.path.join(root, "lib64", "libdcmi.so"),
                    os.path.join(root, "driver", "lib64", "driver", "libdcmi.so"),
                ]
            )

    arch = platform.machine()
    arch_dir = {"x86_64": "x86_64-linux", "aarch64": "aarch64-linux"}.get(arch)
    if arch_dir:
        candidates.extend(
            [
                f"/usr/local/Ascend/ascend-toolkit/latest/{arch_dir}/lib64/libdcmi.so",
                f"/usr/local/Ascend/ascend-toolkit/latest/{arch_dir}/lib64/driver/libdcmi.so",
            ]
        )

    candidates.extend(
        [
            "/usr/local/Ascend/driver/lib64/driver/libdcmi.so",
            "/usr/local/Ascend/driver/lib64/libdcmi.so",
            "/usr/local/Ascend/driver/lib64/lib64/libdcmi.so",
        ]
    )

    # Some installations use a versioned driver directory.  Keep this narrow
    # to Ascend's standard prefix instead of scanning the whole filesystem.
    candidates.extend(
        glob.glob("/usr/local/Ascend/*/driver/lib64/driver/libdcmi.so")
    )
    candidates.extend(glob.glob("/usr/local/Ascend/*/lib64/driver/libdcmi.so"))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def load_library() -> Any:
    """Load ``libdcmi.so`` or raise :class:`DcmiUnavailable`."""

    if sys.platform != "linux":
        raise DcmiUnavailable("DCMI is only available on Linux")

    errors: list[str] = []
    for candidate in _library_candidates():
        if not os.path.exists(candidate):
            continue
        try:
            return ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")

    discovered = ctypes.util.find_library("dcmi")
    if discovered:
        try:
            return ctypes.CDLL(discovered)
        except OSError as exc:
            errors.append(f"{discovered}: {exc}")

    detail = "; ".join(errors) if errors else "no libdcmi.so found"
    raise DcmiUnavailable(detail)


class DcmiBackend:
    """A direct, read-only wrapper around the CANN DCMI API."""

    def __init__(self, library: Any) -> None:
        self._library = library
        self._functions: dict[str, Any] = {}
        self._devices: list[_DeviceRef] = []
        self._metadata: dict[int, tuple[str, str]] = {}
        self._configure()
        self._initialize()

    def _configure(self) -> None:
        signatures = {
            "dcmi_init": [],
            "dcmi_get_card_num_list": [
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
            ],
            "dcmi_get_device_num_in_card": [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_driver_version": [
                ctypes.POINTER(ctypes.c_char),
                ctypes.c_uint,
            ],
            "dcmi_get_device_chip_info_v2": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_ChipInfoV2),
            ],
            "dcmi_get_device_chip_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_ChipInfo),
            ],
            "dcmi_get_device_pcie_info_v2": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_PcieInfoV2),
            ],
            "dcmi_get_device_hbm_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_HbmInfo),
            ],
            "dcmi_get_device_memory_info_v3": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_MemoryInfoV3),
            ],
            "dcmi_get_device_temperature": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_device_power_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_device_frequency": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint),
            ],
            "dcmi_get_device_ecc_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_EccInfo),
            ],
            "dcmi_get_device_fan_count": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_device_fan_speed": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_device_utilization_rate": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint),
            ],
            "dcmi_get_device_dvpp_ratio_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_DvppRatio),
            ],
            "dcmi_get_device_resource_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_ProcMemInfo),
                ctypes.POINTER(ctypes.c_int),
            ],
            "dcmi_get_pcie_link_bandwidth_info": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_PcieLinkBandwidthInfo),
            ],
        }
        for name, argtypes in signatures.items():
            function = getattr(self._library, name, None)
            if function is not None:
                self._functions[name] = _configure_function(function, argtypes)

    def _call(self, name: str, *args: Any) -> int | None:
        function = self._functions.get(name)
        if function is None:
            return None
        try:
            return int(function(*args))
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    def _initialize(self) -> None:
        if "dcmi_init" not in self._functions:
            raise DcmiUnavailable("dcmi_init is unavailable")
        if self._call("dcmi_init") != DCMI_OK:
            raise DcmiUnavailable("dcmi_init failed")

        card_count = ctypes.c_int()
        card_list = (ctypes.c_int * MAX_CARD_NUM)()
        ret = self._call(
            "dcmi_get_card_num_list",
            ctypes.byref(card_count),
            card_list,
            MAX_CARD_NUM,
        )
        if ret != DCMI_OK:
            raise DcmiUnavailable("dcmi_get_card_num_list failed")

        count = max(0, min(int(card_count.value), MAX_CARD_NUM))
        devices: list[_DeviceRef] = []
        for card_index in range(count):
            card_id = int(card_list[card_index])
            device_count = ctypes.c_int()
            ret = self._call(
                "dcmi_get_device_num_in_card",
                card_id,
                ctypes.byref(device_count),
            )
            if ret != DCMI_OK or device_count.value < 0 or device_count.value > MAX_CARD_NUM:
                raise DcmiUnavailable(
                    f"dcmi_get_device_num_in_card failed for card {card_id}"
                )
            for chip_id in range(int(device_count.value)):
                devices.append(_DeviceRef(len(devices), card_id, chip_id))
        self._devices = devices

    @classmethod
    def load(cls) -> "DcmiBackend":
        """Load and initialize a backend from the host's CANN installation."""

        return cls(load_library())

    def _device(self, index: int) -> _DeviceRef | None:
        if not isinstance(index, int) or index < 0 or index >= len(self._devices):
            return None
        return self._devices[index]

    def _struct(self, name: str, index: int, structure: Any) -> Any | None:
        ref = self._device(index)
        if ref is None:
            return None
        ret = self._call(name, ref.card_id, ref.chip_id, ctypes.byref(structure))
        return structure if ret == DCMI_OK else None

    def count(self) -> int:
        return len(self._devices)

    def uuid(self, index: int) -> str | None:
        ref = self._device(index)
        return None if ref is None else f"ASCEND-{ref.card_id:02d}-{ref.chip_id:02d}"

    def name(self, index: int) -> str:
        ref = self._device(index)
        if ref is None:
            return NA
        cached = self._metadata.get(index)
        if cached is not None and cached[0]:
            return cached[0]

        name = ""
        chip_info = _ChipInfoV2()
        if self._struct("dcmi_get_device_chip_info_v2", index, chip_info) is not None:
            name = _decode_char_array(chip_info.npu_name) or _decode_char_array(
                chip_info.chip_name
            )
        if not name:
            legacy_info = _ChipInfo()
            if self._struct("dcmi_get_device_chip_info", index, legacy_info) is not None:
                name = _decode_char_array(legacy_info.chip_name)

        bus_id = cached[1] if cached is not None else ""
        self._metadata[index] = (name, bus_id)
        return name or NA

    def bus_id(self, index: int) -> str:
        cached = self._metadata.get(index)
        if cached is not None and cached[1]:
            return cached[1]

        pcie = _PcieInfoV2()
        if self._struct("dcmi_get_device_pcie_info_v2", index, pcie) is None:
            return NA
        if min(pcie.domain, pcie.bdf_busid, pcie.bdf_deviceid, pcie.bdf_funcid) < 0:
            return NA
        value = f"{int(pcie.domain):04X}:{int(pcie.bdf_busid):02X}:{int(pcie.bdf_deviceid):02X}.{int(pcie.bdf_funcid):X}"
        name = cached[0] if cached is not None else ""
        self._metadata[index] = (name, value)
        return value

    @staticmethod
    def _valid_percentage(value: int) -> int | None:
        return value if 0 <= value <= 100 else None

    def hbm_info(self, index: int) -> HbmInfo:
        """Return HBM capacity and auxiliary telemetry in normalized units.

        DCMI reports capacity/usage in MiB, frequency in MHz, temperature in
        degrees Celsius, and bandwidth utilization in percent.
        """

        hbm = _HbmInfo()
        if self._struct("dcmi_get_device_hbm_info", index, hbm) is None:
            return HbmInfo(NA, NA, NA, NA, NA)
        total = int(hbm.memory_size) * 1024 * 1024
        used = int(hbm.memory_usage) * 1024 * 1024
        frequency = int(hbm.freq)
        temperature = int(hbm.temp)
        bandwidth = self._valid_percentage(int(hbm.bandwith_util_rate))
        if total <= 0 or used < 0:
            return HbmInfo(NA, NA, NA, NA, NA)
        return HbmInfo(
            total,
            frequency if frequency >= 0 else NA,
            min(used, total),
            temperature if temperature >= 0 else NA,
            NA if bandwidth is None else bandwidth,
        )

    def memory_info(self, index: int) -> MemoryInfo:
        hbm = self.hbm_info(index)
        if isinstance(hbm.total, int) and isinstance(hbm.used, int):
            return MemoryInfo(hbm.total, max(hbm.total - hbm.used, 0), hbm.used)

        memory = _MemoryInfoV3()
        if self._struct("dcmi_get_device_memory_info_v3", index, memory) is not None:
            total = int(memory.memory_size) * 1024 * 1024
            available = int(memory.memory_available) * 1024 * 1024
            if total >= 0 and available >= 0:
                used = max(total - available, 0)
                return MemoryInfo(total, max(available, 0), used)
        return MemoryInfo(NA, NA, NA)

    def _frequency(self, index: int, selector: int) -> int | None:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_frequency" not in self._functions:
            return None
        value = ctypes.c_uint()
        ret = self._call(
            "dcmi_get_device_frequency",
            ref.card_id,
            ref.chip_id,
            selector,
            ctypes.byref(value),
        )
        return int(value.value) if ret == DCMI_OK else None

    def clock_infos(self, index: int) -> ClockInfos:
        """Return current NPU and memory clocks in MHz.

        Ascend has no graphics/video clocks equivalent to CUDA's fields, so
        those two members remain ``N/A``.  The AICORE clock is the closest
        equivalent to nvitop's SM clock.
        """

        sm = self._frequency(index, DCMI_FREQ_AICORE_CURRENT)
        memory = self._frequency(index, DCMI_FREQ_HBM)
        if memory is None:
            memory = self._frequency(index, DCMI_FREQ_DDR)
        if memory is None:
            hbm = self.hbm_info(index)
            memory = hbm.frequency if isinstance(hbm.frequency, int) else None
        return ClockInfos(
            graphics=NA,
            sm=NA if sm is None or sm < 0 else sm,
            memory=NA if memory is None or memory < 0 else memory,
            video=NA,
        )

    def max_clock_infos(self, index: int) -> ClockInfos:
        sm = self._frequency(index, DCMI_FREQ_AICORE_MAX)
        return ClockInfos(
            graphics=NA,
            sm=NA if sm is None or sm < 0 else sm,
            memory=NA,
            video=NA,
        )

    def clock_speed_infos(self, index: int) -> ClockSpeedInfos:
        return ClockSpeedInfos(
            current=self.clock_infos(index),
            max=self.max_clock_infos(index),
        )

    def fan_speed(self, index: int) -> int | str:
        """Return average fan speed as a percentage.

        DCMI exposes RPM while nputop's public compatibility API follows
        nvitop and exposes a percentage.  The DCMI documentation defines
        18,000 RPM as the nominal maximum.
        """

        ref = self._device(index)
        if ref is None:
            return NA
        count = ctypes.c_int()
        if self._call(
            "dcmi_get_device_fan_count",
            ref.card_id,
            ref.chip_id,
            ctypes.byref(count),
        ) != DCMI_OK or count.value <= 0:
            return NA
        speed = ctypes.c_int()
        # fan_id 0 is explicitly documented as the average of all fans.
        if self._call(
            "dcmi_get_device_fan_speed",
            ref.card_id,
            ref.chip_id,
            0,
            ctypes.byref(speed),
        ) != DCMI_OK or speed.value < 0:
            return NA
        return round(100 * int(speed.value) / DCMI_FAN_MAX_RPM)

    def dvpp_utilization(self, index: int) -> DvppUtilization:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_dvpp_ratio_info" not in self._functions:
            return DvppUtilization(NA, NA)
        ratio = _DvppRatio()
        ret = self._call(
            "dcmi_get_device_dvpp_ratio_info",
            ref.card_id,
            ref.chip_id,
            ctypes.byref(ratio),
        )
        if ret != DCMI_OK:
            return DvppUtilization(NA, NA)
        decoder = self._valid_percentage(int(ratio.vdec_ratio))
        encoder = self._valid_percentage(int(ratio.venc_ratio))
        return DvppUtilization(
            NA if decoder is None else decoder,
            NA if encoder is None else encoder,
        )

    def ecc_info(self, index: int) -> EccInfo:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_ecc_info" not in self._functions:
            return EccInfo(NA, NA, NA, NA, NA, NA, NA)
        info = _EccInfo()
        ret = self._call(
            "dcmi_get_device_ecc_info",
            ref.card_id,
            ref.chip_id,
            DCMI_DEVICE_TYPE_HBM,
            ctypes.byref(info),
        )
        if ret != DCMI_OK:
            return EccInfo(NA, NA, NA, NA, NA, NA, NA)
        return EccInfo(
            int(info.enable_flag),
            int(info.single_bit_error_cnt),
            int(info.double_bit_error_cnt),
            int(info.total_single_bit_error_cnt),
            int(info.total_double_bit_error_cnt),
            int(info.single_bit_isolated_pages_cnt),
            int(info.double_bit_isolated_pages_cnt),
        )

    def total_volatile_uncorrected_ecc_errors(self, index: int) -> int | str:
        info = self.ecc_info(index)
        # ``double_bit`` is the volatile counter (cleared after restart).
        # ``total_double_bit`` is a lifecycle aggregate and therefore does
        # not match nvitop's ``volatile`` field.
        return info.double_bit if isinstance(info.double_bit, int) else NA

    def pcie_throughput(self, index: int) -> ThroughputInfo:
        """Return PCIe TX/RX throughput in KiB/s.

        The DCMI structure reports average bandwidth in MiB/ms.  Convert it
        to the KiB/s unit used by nvitop's public API.  Each direction sums
        posted, non-posted, and completion traffic.
        """

        ref = self._device(index)
        if ref is None or "dcmi_get_pcie_link_bandwidth_info" not in self._functions:
            return ThroughputInfo(NA, NA)
        info = _PcieLinkBandwidthInfo()
        info.profiling_time = PCIE_PROFILING_TIME_MS
        ret = self._call(
            "dcmi_get_pcie_link_bandwidth_info",
            ref.card_id,
            ref.chip_id,
            ctypes.byref(info),
        )
        if ret != DCMI_OK:
            return ThroughputInfo(NA, NA)
        # Index 2 is the documented average (MIN/MAX/AVG).
        tx = int(info.tx_p_bw[2] + info.tx_np_bw[2] + info.tx_cpl_bw[2])
        rx = int(info.rx_p_bw[2] + info.rx_np_bw[2] + info.rx_cpl_bw[2])
        scale = 1024 * 1000  # MiB/ms -> KiB/s
        return ThroughputInfo(tx * scale, rx * scale)

    def _utilization(self, index: int, selector: int) -> int | None:
        ref = self._device(index)
        function = self._functions.get("dcmi_get_device_utilization_rate")
        if ref is None or function is None:
            return None
        value = ctypes.c_uint()
        ret = self._call(
            "dcmi_get_device_utilization_rate",
            ref.card_id,
            ref.chip_id,
            selector,
            ctypes.byref(value),
        )
        if ret != DCMI_OK or value.value > 100:
            return None
        return int(value.value)

    def utilization_rates(self, index: int) -> UtilizationRates:
        npu = self._utilization(index, DCMI_UTILIZATION_RATE_NPU)
        memory = self._utilization(index, DCMI_UTILIZATION_RATE_HBM)
        if memory is None:
            memory = self._utilization(index, DCMI_UTILIZATION_RATE_DDR)
        if memory is None:
            info = self.memory_info(index)
            if isinstance(info.used, int) and isinstance(info.total, int) and info.total:
                memory = round(100 * info.used / info.total)
        bandwidth = self._utilization(index, DCMI_UTILIZATION_RATE_HBM_BANDWIDTH)
        aicpu = self._utilization(index, DCMI_UTILIZATION_RATE_AICPU)
        return UtilizationRates(
            NA if npu is None else npu,
            NA if memory is None else memory,
            NA if bandwidth is None else bandwidth,
            NA if aicpu is None else aicpu,
        )

    def temperature(self, index: int) -> int | str:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_temperature" not in self._functions:
            return NA
        value = ctypes.c_int()
        ret = self._call(
            "dcmi_get_device_temperature", ref.card_id, ref.chip_id, ctypes.byref(value)
        )
        return int(value.value) if ret == DCMI_OK else NA

    def power_usage(self, index: int) -> int | str:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_power_info" not in self._functions:
            return NA
        value = ctypes.c_int()
        ret = self._call(
            "dcmi_get_device_power_info", ref.card_id, ref.chip_id, ctypes.byref(value)
        )
        # DCMI reports 0.1 W; nputop's compatibility API reports mW.
        return int(value.value) * 100 if ret == DCMI_OK and value.value >= 0 else NA

    def process_info(self, index: int) -> tuple[ProcessInfo, ...]:
        ref = self._device(index)
        if ref is None or "dcmi_get_device_resource_info" not in self._functions:
            return ()
        entries = (_ProcMemInfo * MAX_PROC_NUM_IN_DEVICE)()
        count = ctypes.c_int()
        ret = self._call(
            "dcmi_get_device_resource_info",
            ref.card_id,
            ref.chip_id,
            entries,
            ctypes.byref(count),
        )
        if ret != DCMI_OK:
            return ()
        size = max(0, min(int(count.value), MAX_PROC_NUM_IN_DEVICE))
        return tuple(
            ProcessInfo(int(entries[i].proc_id), int(entries[i].proc_mem_usage))
            for i in range(size)
            if int(entries[i].proc_id) > 0 and int(entries[i].proc_mem_usage) >= 0
        )

    def driver_version(self) -> str:
        function = self._functions.get("dcmi_get_driver_version")
        if function is None:
            return NA
        buffer = ctypes.create_string_buffer(256)
        ret = self._call("dcmi_get_driver_version", buffer, ctypes.sizeof(buffer))
        return _decode_char_array(buffer.value) if ret == DCMI_OK else NA


def create_backend(library: Any | None = None) -> DcmiBackend:
    """Create a DCMI backend, optionally from an injected test library."""

    return DcmiBackend(load_library() if library is None else library)


__all__ = [
    "DCMI_OK",
    "ClockInfos",
    "ClockSpeedInfos",
    "DcmiBackend",
    "DcmiUnavailable",
    "DvppUtilization",
    "EccInfo",
    "HbmInfo",
    "MemoryInfo",
    "NA",
    "PCIE_PROFILING_TIME_MS",
    "ProcessInfo",
    "ThroughputInfo",
    "UtilizationRates",
    "create_backend",
    "load_library",
]
