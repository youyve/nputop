# pylint: disable=too-many-lines

from __future__ import annotations

import contextlib
import functools
import multiprocessing as mp
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Generator, Iterable, NamedTuple, overload

from nputop.api import libnvml
from nputop.api.process import NpuProcess
from nputop.api.utils import (
    NA,
    UINT_MAX,
    NaType,
    Snapshot,
    boolify,
    bytes2human,
    memoize_when_activated,
)


if TYPE_CHECKING:
    from collections.abc import Hashable

    from typing_extensions import Literal  # Python 3.8+
    from typing_extensions import Self  # Python 3.11+


__all__ = [
    'Device',
    'PhysicalDevice',
    'CudaDevice',
    'parse_cuda_visible_devices',
    'normalize_cuda_visible_devices',
]

# Class definitions ################################################################################


class MemoryInfo(NamedTuple):  # in bytes # pylint: disable=missing-class-docstring
    total: int | NaType
    free: int | NaType
    used: int | NaType


class ClockInfos(NamedTuple):  # in MHz # pylint: disable=missing-class-docstring
    graphics: int | NaType
    sm: int | NaType
    memory: int | NaType
    video: int | NaType


class ClockSpeedInfos(NamedTuple):  # pylint: disable=missing-class-docstring
    current: ClockInfos
    max: ClockInfos


class UtilizationRates(NamedTuple):  # in percentage # pylint: disable=missing-class-docstring
    npu: int | NaType
    memory: int | NaType
    encoder: int | NaType
    decoder: int | NaType


class ThroughputInfo(NamedTuple):  # in KiB/s # pylint: disable=missing-class-docstring
    tx: int | NaType
    rx: int | NaType

    @property
    def transmit(self) -> int | NaType:
        """Alias of :attr:`tx`."""
        return self.tx

    @property
    def receive(self) -> int | NaType:
        """Alias of :attr:`rx`."""
        return self.rx


# pylint: disable-next=missing-class-docstring,too-few-public-methods
class ValueOmitted:
    def __repr__(self) -> str:
        return '<VALUE OMITTED>'


_VALUE_OMITTED: str = ValueOmitted()  # type: ignore[assignment]
del ValueOmitted


class Device:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    UUID_PATTERN: re.Pattern = re.compile(
        r"""^  # full match
        (?:(?P<MigMode>MIG)-)?                                 # prefix for MIG UUID
        (?:(?P<NpuUuid>NPU)-)?                                 # prefix for NPU UUID
        (?(MigMode)|(?(NpuUuid)|NPU-))                         # always have a prefix
        (?P<UUID>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})  # UUID for the NPU/MIG device in lower case
        # Suffix for MIG device while using NPU UUID with NPU instance (GI) ID and compute instance (CI) ID
        (?(MigMode)                                            # match only when the MIG prefix matches
            (?(NpuUuid)                                        # match only when provide with NPU UUID
                /(?P<NpuInstanceId>\d+)                        # GI ID of the MIG device
                /(?P<ComputeInstanceId>\d+)                    # CI ID of the MIG device
            |)
        |)
        $""",  # full match
        flags=re.VERBOSE,
    )

    NPU_PROCESS_CLASS: type[NpuProcess] = NpuProcess
    cuda: type[CudaDevice] = None  # type: ignore[assignment] # defined in below
    """Shortcut for class :class:`CudaDevice`."""

    _nvml_index: int | tuple[int, int]

    @classmethod
    def is_available(cls) -> bool:
        """Test whether there are any devices and the NVML library is successfully loaded."""
        try:
            return cls.count() > 0
        except libnvml.NVMLError:
            return False

    @staticmethod
    def driver_version() -> str | NaType:
        """The version of the installed NVIDIA display driver. This is an alphanumeric string.

        Command line equivalent:

        .. code:: bash

            nvidia-smi --id=0 --format=csv,noheader,nounits --query-npu=driver_version

        Raises:
            libnvml.NVMLError_LibraryNotFound:
                If cannot find the NVML library, usually the NVIDIA driver is not installed.
            libnvml.NVMLError_DriverNotLoaded:
                If NVIDIA driver is not loaded.
            libnvml.NVMLError_LibRmVersionMismatch:
                If RM detects a driver/library version mismatch, usually after an upgrade for NVIDIA
                driver without reloading the kernel module.
        """
        return libnvml.nvmlQuery('nvmlSystemGetDriverVersion')

    @staticmethod
    def cuda_driver_version() -> str | NaType:
        """The maximum CUDA version supported by the NVIDIA display driver. This is an alphanumeric string.

        This can be different from the version of the CUDA Runtime. See also :meth:`cuda_runtime_version`.

        Returns: Union[str, NaType]
            The maximum CUDA version supported by the NVIDIA display driver.

        Raises:
            libnvml.NVMLError_LibraryNotFound:
                If cannot find the NVML library, usually the NVIDIA driver is not installed.
            libnvml.NVMLError_DriverNotLoaded:
                If NVIDIA driver is not loaded.
            libnvml.NVMLError_LibRmVersionMismatch:
                If RM detects a driver/library version mismatch, usually after an upgrade for NVIDIA
                driver without reloading the kernel module.
        """
        cuda_driver_version = libnvml.nvmlQuery('nvmlSystemGetCudaDriverVersion')
        if libnvml.nvmlCheckReturn(cuda_driver_version, int):
            major = cuda_driver_version // 1000
            minor = (cuda_driver_version % 1000) // 10
            revision = cuda_driver_version % 10
            if revision == 0:
                return f'{major}.{minor}'
            return f'{major}.{minor}.{revision}'
        return NA

    max_cuda_version = cuda_driver_version

    @classmethod
    def count(cls) -> int:
        """The number of NVIDIA NPUs in the system.

        Command line equivalent:

        .. code:: bash

            nvidia-smi --id=0 --format=csv,noheader,nounits --query-npu=count

        Raises:
            libnvml.NVMLError_LibraryNotFound:
                If cannot find the NVML library, usually the NVIDIA driver is not installed.
            libnvml.NVMLError_DriverNotLoaded:
                If NVIDIA driver is not loaded.
            libnvml.NVMLError_LibRmVersionMismatch:
                If RM detects a driver/library version mismatch, usually after an upgrade for NVIDIA
                driver without reloading the kernel module.
        """
        return libnvml.nvmlQuery('nvmlDeviceGetCount', default=0)

    @classmethod
    def all(cls) -> list[PhysicalDevice]:
        """Return a list of all physical devices in the system."""
        return cls.from_indices()  # type: ignore[return-value]

    @classmethod
    def from_indices(
        cls,
        indices: int | Iterable[int | tuple[int, int]] | None = None,
    ) -> list[PhysicalDevice]:
        if indices is None:
            try:
                indices = range(cls.count())
            except libnvml.NVMLError:
                return []

        if isinstance(indices, int):
            indices = [indices]

        return list(map(cls, indices))  # type: ignore[arg-type]

    @staticmethod
    def from_cuda_visible_devices() -> list[CudaDevice]:
        # pylint: disable=line-too-long
        visible_device_indices = Device.parse_cuda_visible_devices()

        device_index: int | tuple[int, int]
        cuda_devices: list[CudaDevice] = []
        for cuda_index, device_index in enumerate(visible_device_indices):  # type: ignore[assignment]
            cuda_devices.append(CudaDevice(cuda_index, nvml_index=device_index))

        return cuda_devices

    @staticmethod
    def from_cuda_indices(
        cuda_indices: int | Iterable[int] | None = None,
    ) -> list[CudaDevice]:
        # pylint: disable=line-too-long
        cuda_devices = Device.from_cuda_visible_devices()
        if cuda_indices is None:
            return cuda_devices

        if isinstance(cuda_indices, int):
            cuda_indices = [cuda_indices]

        cuda_indices = list(cuda_indices)
        cuda_device_count = len(cuda_devices)

        devices = []
        for cuda_index in cuda_indices:
            if not 0 <= cuda_index < cuda_device_count:
                raise RuntimeError(f'CUDA Error: invalid device ordinal: {cuda_index!r}.')
            device = cuda_devices[cuda_index]
            devices.append(device)

        return devices

    @staticmethod
    def parse_cuda_visible_devices(
        cuda_visible_devices: str | None = _VALUE_OMITTED,
    ) -> list[int] | list[tuple[int, int]]:
        # pylint: disable=line-too-long
        return parse_cuda_visible_devices(cuda_visible_devices)

    @staticmethod
    def normalize_cuda_visible_devices(cuda_visible_devices: str | None = _VALUE_OMITTED) -> str:
        # pylint: disable=line-too-long
        return normalize_cuda_visible_devices(cuda_visible_devices)

    def __new__(
        cls,
        index: int | tuple[int, int] | str | None = None,
        *,
        uuid: str | None = None,
        bus_id: str | None = None,
    ) -> Self:
        if (index, uuid, bus_id).count(None) != 2:
            raise TypeError(
                f'Device(index=None, uuid=None, bus_id=None) takes 1 non-None arguments '
                f'but (index, uuid, bus_id) = {(index, uuid, bus_id)!r} were given',
            )

        if cls is not Device:
            # Use the subclass type if the type is explicitly specified
            return super().__new__(cls)

        # Auto subclass type inference logic goes here when `cls` is `Device` (e.g., calls `Device(...)`)
        match: re.Match | None = None
        if isinstance(index, str):
            match = cls.UUID_PATTERN.match(index)
            if match is not None:  # passed by UUID
                index, uuid = None, index
        elif isinstance(uuid, str):
            match = cls.UUID_PATTERN.match(uuid)
            
        return super().__new__(PhysicalDevice)  # type: ignore[return-value]

    def __init__(
        self,
        index: int | str | None = None,
        *,
        uuid: str | None = None,
        bus_id: str | None = None,
    ) -> None:
        if isinstance(index, str) and self.UUID_PATTERN.match(index) is not None:  # passed by UUID
            index, uuid = None, index

        index, uuid, bus_id = (
            arg.encode() if isinstance(arg, str) else arg for arg in (index, uuid, bus_id)
        )

        self._name: str = NA
        self._uuid: str = NA
        self._bus_id: str = NA
        self._memory_total: int | NaType = NA
        self._memory_total_human: str = NA
        self._nvlink_link_count: int | None = None
        self._nvlink_throughput_counters: tuple[tuple[int | NaType, int]] | None = None
        self._cuda_index: int | None = None
        self._cuda_compute_capability: tuple[int, int] | NaType | None = None

        if index is not None:
            self._nvml_index = index  # type: ignore[assignment]
            try:
                self._handle = libnvml.nvmlQuery(
                    'nvmlDeviceGetHandleByIndex',
                    index,
                    ignore_errors=False,
                )
            except libnvml.NVMLError_NpuIsLost:
                self._handle = None
                self._name = 'ERROR: NPU is Lost'
            except libnvml.NVMLError_Unknown:
                self._handle = None
                self._name = 'ERROR: Unknown'
        else:
            try:
                if uuid is not None:
                    self._handle = libnvml.nvmlQuery(
                        'nvmlDeviceGetHandleByUUID',
                        uuid,
                        ignore_errors=False,
                    )
                else:
                    self._handle = libnvml.nvmlQuery(
                        'nvmlDeviceGetHandleByPciBusId',
                        bus_id,
                        ignore_errors=False,
                    )
            except libnvml.NVMLError_NpuIsLost:
                self._handle = None
                self._nvml_index = NA  # type: ignore[assignment]
                self._name = 'ERROR: NPU is Lost'
            except libnvml.NVMLError_Unknown:
                self._handle = None
                self._nvml_index = NA  # type: ignore[assignment]
                self._name = 'ERROR: Unknown'
            else:
                self._nvml_index = libnvml.nvmlQuery('nvmlDeviceGetIndex', self._handle)

        self._max_clock_infos: ClockInfos = ClockInfos(graphics=NA, sm=NA, memory=NA, video=NA)
        self._lock: threading.RLock = threading.RLock()

        self._ident: tuple[Hashable, str] = (self.index, self.uuid())
        self._hash: int | None = None

    def __repr__(self) -> str:
        """Return a string representation of the device."""
        return '{}(index={}, name={!r}, total_memory={})'.format(  # noqa: UP032
            self.__class__.__name__,
            self.index,
            self.name(),
            self.memory_total_human(),
        )

    def __eq__(self, other: object) -> bool:
        """Test equality to other object."""
        if not isinstance(other, Device):
            return NotImplemented
        return self._ident == other._ident

    def __hash__(self) -> int:
        """Return a hash value of the device."""
        if self._hash is None:
            self._hash = hash(self._ident)
        return self._hash

    def __getattr__(self, name: str) -> Any | Callable[..., Any]:
        # pylint: disable=line-too-long
        try:
            return super().__getattr__(name)  # type: ignore[misc]
        except AttributeError:
            if name == '_cache':
                raise
            if self._handle is None:
                return lambda: NA

            match = libnvml.VERSIONED_PATTERN.match(name)
            if match is not None:
                name = match.group('name')
                suffix = match.group('suffix')
            else:
                suffix = ''

            try:
                pascal_case = name.title().replace('_', '')
                func = getattr(libnvml, 'nvmlDeviceGet' + pascal_case + suffix)
            except AttributeError:
                pascal_case = ''.join(
                    part[:1].upper() + part[1:] for part in filter(None, name.split('_'))
                )
                func = getattr(libnvml, 'nvmlDeviceGet' + pascal_case + suffix)

            def attribute(*args: Any, **kwargs: Any) -> Any:
                try:
                    return libnvml.nvmlQuery(
                        func,
                        self._handle,
                        *args,
                        **kwargs,
                        ignore_errors=False,
                    )
                except libnvml.NVMLError_NotSupported:
                    return NA

            attribute.__name__ = name
            attribute.__qualname__ = f'{self.__class__.__name__}.{name}'
            setattr(self, name, attribute)
            return attribute

    def __reduce__(self) -> tuple[type[Device], tuple[int | tuple[int, int]]]:
        """Return state information for pickling."""
        return self.__class__, (self._nvml_index,)

    @property
    def index(self) -> int | tuple[int, int]:
        return self._nvml_index

    @property
    def nvml_index(self) -> int | tuple[int, int]:
        return self._nvml_index

    @property
    def physical_index(self) -> int:
        return self._nvml_index  # type: ignore[return-value]

    @property
    def handle(self) -> libnvml.c_nvmlDevice_t:
        """The NVML device handle."""
        return self._handle

    @property
    def cuda_index(self) -> int:
        if self._cuda_index is None:
            visible_device_indices = self.parse_cuda_visible_devices()
            try:
                self._cuda_index = visible_device_indices.index(self.index)  # type: ignore[arg-type]
            except ValueError as ex:
                raise RuntimeError(
                    f'CUDA Error: Device(index={self.index}) is not visible to CUDA applications',
                ) from ex

        return self._cuda_index

    def name(self) -> str | NaType:
        if self._name is NA:
            self._name = libnvml.nvmlQuery('nvmlDeviceGetName', self.handle)
        return self._name

    def uuid(self) -> str | NaType:
        if self._uuid is NA:
            self._uuid = libnvml.nvmlQuery('nvmlDeviceGetUUID', self.handle)
        return self._uuid

    def bus_id(self) -> str | NaType:
        if self._bus_id is NA:
            self._bus_id = libnvml.nvmlQuery(
                lambda handle: libnvml.nvmlDeviceGetPciInfo(handle).busId,
                self.handle,
            )
        return self._bus_id

    def serial(self) -> str | NaType:
        return libnvml.nvmlQuery('nvmlDeviceGetSerial', self.handle)

    @memoize_when_activated
    def memory_info(self) -> MemoryInfo:  # in bytes
        memory_info = libnvml.nvmlQuery('nvmlDeviceGetMemoryInfo', self.handle)
        if libnvml.nvmlCheckReturn(memory_info):
            return MemoryInfo(total=memory_info.total, free=memory_info.free, used=memory_info.used)
        return MemoryInfo(total=NA, free=NA, used=NA)

    def memory_total(self) -> int | NaType:  # in bytes
        if self._memory_total is NA:
            self._memory_total = self.memory_info().total
        return self._memory_total

    def memory_used(self) -> int | NaType:  # in bytes
        return self.memory_info().used

    def memory_free(self) -> int | NaType:  # in bytes
        return self.memory_info().free

    def memory_total_human(self) -> str | NaType:  # in human readable
        if self._memory_total_human is NA:
            self._memory_total_human = bytes2human(self.memory_total())
        return self._memory_total_human

    def memory_used_human(self) -> str | NaType:  # in human readable
        # pylint: disable=line-too-long
        return bytes2human(self.memory_used())

    def memory_free_human(self) -> str | NaType:  # in human readable
        return bytes2human(self.memory_free())

    def memory_percent(self) -> float | NaType:  # in percentage
        memory_info = self.memory_info()
        if libnvml.nvmlCheckReturn(memory_info.used, int) and libnvml.nvmlCheckReturn(
            memory_info.total,
            int,
        ):
            return round(100.0 * memory_info.used / memory_info.total, 1)
        return NA

    def memory_usage(self) -> str:  # string of used memory over total memory (in human readable)
        # pylint: disable=line-too-long
        return f'{self.memory_used_human()} / {self.memory_total_human()}'

    @memoize_when_activated
    def bar1_memory_info(self) -> MemoryInfo:  # in bytes
        # pylint: disable=line-too-long
        memory_info = libnvml.nvmlQuery('nvmlDeviceGetBAR1MemoryInfo', self.handle)
        if libnvml.nvmlCheckReturn(memory_info):
            return MemoryInfo(
                total=memory_info.bar1Total,
                free=memory_info.bar1Free,
                used=memory_info.bar1Used,
            )
        return MemoryInfo(total=NA, free=NA, used=NA)

    def bar1_memory_total(self) -> int | NaType:  # in bytes
        return self.bar1_memory_info().total

    def bar1_memory_used(self) -> int | NaType:  # in bytes
        return self.bar1_memory_info().used

    def bar1_memory_free(self) -> int | NaType:  # in bytes
        return self.bar1_memory_info().free

    def bar1_memory_total_human(self) -> str | NaType:  # in human readable
        return bytes2human(self.bar1_memory_total())

    def bar1_memory_used_human(self) -> str | NaType:  # in human readable
        return bytes2human(self.bar1_memory_used())

    def bar1_memory_free_human(self) -> str | NaType:  # in human readable
        return bytes2human(self.bar1_memory_free())

    def bar1_memory_percent(self) -> float | NaType:  # in percentage
        # pylint: disable=line-too-long
        memory_info = self.bar1_memory_info()
        if libnvml.nvmlCheckReturn(memory_info.used, int) and libnvml.nvmlCheckReturn(
            memory_info.total,
            int,
        ):
            return round(100.0 * memory_info.used / memory_info.total, 1)
        return NA

    def bar1_memory_usage(self) -> str:  # in human readable
        # pylint: disable=line-too-long
        return f'{self.bar1_memory_used_human()} / {self.bar1_memory_total_human()}'

    @memoize_when_activated
    def utilization_rates(self) -> UtilizationRates:  # in percentage
        # pylint: disable=line-too-long
        npu, memory, encoder, decoder = NA, NA, NA, NA

        utilization_rates = libnvml.nvmlQuery('nvmlDeviceGetUtilizationRates', self.handle)
        if libnvml.nvmlCheckReturn(utilization_rates):
            npu, memory = utilization_rates.gpu, utilization_rates.memory

        encoder_utilization = libnvml.nvmlQuery('nvmlDeviceGetEncoderUtilization', self.handle)
        if libnvml.nvmlCheckReturn(encoder_utilization, list) and len(encoder_utilization) > 0:
            encoder = encoder_utilization[0]

        decoder_utilization = libnvml.nvmlQuery('nvmlDeviceGetDecoderUtilization', self.handle)
        if libnvml.nvmlCheckReturn(decoder_utilization, list) and len(decoder_utilization) > 0:
            decoder = decoder_utilization[0]

        return UtilizationRates(npu=npu, memory=memory, encoder=encoder, decoder=decoder)

    def npu_utilization(self) -> int | NaType:  # in percentage
        return self.utilization_rates().npu

    npu_percent = npu_utilization  # in percentage

    def memory_utilization(self) -> int | NaType:  # in percentage
        # pylint: disable=line-too-long
        return self.utilization_rates().memory

    def encoder_utilization(self) -> int | NaType:  # in percentage
        return self.utilization_rates().encoder

    def decoder_utilization(self) -> int | NaType:  # in percentage
        return self.utilization_rates().decoder

    @memoize_when_activated
    def clock_infos(self) -> ClockInfos:  # in MHz
        # pylint: disable=line-too-long
        return ClockInfos(
            graphics=libnvml.nvmlQuery(
                'nvmlDeviceGetClockInfo',
                self.handle,
                libnvml.NVML_CLOCK_GRAPHICS,
            ),
            sm=libnvml.nvmlQuery('nvmlDeviceGetClockInfo', self.handle, libnvml.NVML_CLOCK_SM),
            memory=libnvml.nvmlQuery('nvmlDeviceGetClockInfo', self.handle, libnvml.NVML_CLOCK_MEM),
            video=libnvml.nvmlQuery(
                'nvmlDeviceGetClockInfo',
                self.handle,
                libnvml.NVML_CLOCK_VIDEO,
            ),
        )

    clocks = clock_infos

    @memoize_when_activated
    def max_clock_infos(self) -> ClockInfos:  # in MHz
        # pylint: disable=line-too-long
        clock_infos = self._max_clock_infos._asdict()
        for name, clock in clock_infos.items():
            if clock is NA:
                clock_type = getattr(
                    libnvml,
                    'NVML_CLOCK_{}'.format(name.replace('memory', 'mem').upper()),
                )
                clock = libnvml.nvmlQuery('nvmlDeviceGetMaxClockInfo', self.handle, clock_type)
                clock_infos[name] = clock
        self._max_clock_infos = ClockInfos(**clock_infos)
        return self._max_clock_infos

    max_clocks = max_clock_infos

    def clock_speed_infos(self) -> ClockSpeedInfos:  # in MHz
        return ClockSpeedInfos(current=self.clock_infos(), max=self.max_clock_infos())

    def graphics_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.clock_infos().graphics

    def sm_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.clock_infos().sm

    def memory_clock(self) -> int | NaType:  # in MHz
        return self.clock_infos().memory

    def video_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.clock_infos().video

    def max_graphics_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.max_clock_infos().graphics

    def max_sm_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.max_clock_infos().sm

    def max_memory_clock(self) -> int | NaType:  # in MHz
        return self.max_clock_infos().memory

    def max_video_clock(self) -> int | NaType:  # in MHz
        # pylint: disable=line-too-long
        return self.max_clock_infos().video

    def fan_speed(self) -> int | NaType:  # in percentage
        # pylint: disable=line-too-long
        return libnvml.nvmlQuery('nvmlDeviceGetFanSpeed', self.handle)

    def temperature(self) -> int | NaType:  # in Celsius
        return libnvml.nvmlQuery(
            'nvmlDeviceGetTemperature',
            self.handle,
            libnvml.NVML_TEMPERATURE_GPU,
        )

    @memoize_when_activated
    def power_usage(self) -> int | NaType:  # in milliwatts (mW)
        return libnvml.nvmlQuery('nvmlDeviceGetPowerUsage', self.handle)

    power_draw = power_usage  # in milliwatts (mW)

    @memoize_when_activated
    def power_limit(self) -> int | NaType:  # in milliwatts (mW)
        return libnvml.nvmlQuery('nvmlDeviceGetPowerManagementLimit', self.handle)

    def power_status(self) -> str:  # string of power usage over power limit in watts (W)
        # pylint: disable=line-too-long
        power_usage = self.power_usage()
        power_limit = self.power_limit()
        if libnvml.nvmlCheckReturn(power_usage, int):
            power_usage = f'{round(power_usage / 1000)}W'  # type: ignore[assignment]
        if libnvml.nvmlCheckReturn(power_limit, int):
            power_limit = f'{round(power_limit / 1000)}W'  # type: ignore[assignment]
        return f'{power_usage} / {power_limit}'

    def pcie_throughput(self) -> ThroughputInfo:  # in KiB/s
        return ThroughputInfo(tx=self.pcie_tx_throughput(), rx=self.pcie_rx_throughput())

    @memoize_when_activated
    def pcie_tx_throughput(self) -> int | NaType:  # in KiB/s
        return libnvml.nvmlQuery(
            'nvmlDeviceGetPcieThroughput',
            self.handle,
            libnvml.NVML_PCIE_UTIL_RX_BYTES,
        )

    @memoize_when_activated
    def pcie_rx_throughput(self) -> int | NaType:  # in KiB/s
        return libnvml.nvmlQuery(
            'nvmlDeviceGetPcieThroughput',
            self.handle,
            libnvml.NVML_PCIE_UTIL_RX_BYTES,
        )

    def pcie_tx_throughput_human(self) -> str | NaType:  # in human readable
        tx = self.pcie_tx_throughput()
        if libnvml.nvmlCheckReturn(tx, int):
            return f'{bytes2human(tx * 1024)}/s'
        return NA

    def pcie_rx_throughput_human(self) -> str | NaType:  # in human readable
        rx = self.pcie_rx_throughput()
        if libnvml.nvmlCheckReturn(rx, int):
            return f'{bytes2human(rx * 1024)}/s'
        return NA

    def nvlink_link_count(self) -> int:
        if self._nvlink_link_count is None:
            ((nvlink_link_count, _),) = libnvml.nvmlQueryFieldValues(
                self.handle,
                [libnvml.NVML_FI_DEV_NVLINK_LINK_COUNT],
            )
            if libnvml.nvmlCheckReturn(nvlink_link_count, int):
                self._nvlink_link_count = nvlink_link_count  # type: ignore[assignment]
            else:
                self._nvlink_link_count = 0
        return self._nvlink_link_count  # type: ignore[return-value]

    @memoize_when_activated
    def nvlink_throughput(
        self,
        interval: float | None = None,
    ) -> list[ThroughputInfo]:  # in KiB/s
        nvlink_link_count = self.nvlink_link_count()
        if nvlink_link_count == 0:
            return []

        def query_nvlink_throughput_counters() -> tuple[tuple[int | NaType, int]]:
            return tuple(  # type: ignore[return-value]
                libnvml.nvmlQueryFieldValues(
                    self.handle,
                    [  # type: ignore[arg-type]
                        (libnvml.NVML_FI_DEV_NVLINK_THROUGHPUT_DATA_TX, i)
                        for i in range(nvlink_link_count)
                    ]
                    + [
                        (libnvml.NVML_FI_DEV_NVLINK_THROUGHPUT_DATA_RX, i)
                        for i in range(nvlink_link_count)
                    ],
                ),
            )

        if interval is not None:
            if not interval >= 0.0:
                raise ValueError(f'`interval` must be a non-negative number, got {interval!r}.')
            if interval > 0.0:
                self._nvlink_throughput_counters = query_nvlink_throughput_counters()
                time.sleep(interval)

        if self._nvlink_throughput_counters is None:
            self._nvlink_throughput_counters = query_nvlink_throughput_counters()
            time.sleep(0.02)  # 20ms

        old_throughput_counters = self._nvlink_throughput_counters
        new_throughput_counters = query_nvlink_throughput_counters()

        throughputs: list[int | NaType] = []
        for (old_counter, old_timestamp), (new_counter, new_timestamp) in zip(
            old_throughput_counters,
            new_throughput_counters,
        ):
            if (
                libnvml.nvmlCheckReturn(old_counter, int)
                and libnvml.nvmlCheckReturn(new_counter, int)
                and new_timestamp > old_timestamp
            ):
                throughputs.append(
                    round(
                        1_000_000 * (new_counter - old_counter) / (new_timestamp - old_timestamp),
                    ),
                )
            else:
                throughputs.append(NA)

        self._nvlink_throughput_counters = new_throughput_counters
        return [
            ThroughputInfo(tx=tx, rx=rx)
            for tx, rx in zip(throughputs[:nvlink_link_count], throughputs[nvlink_link_count:])
        ]

    def nvlink_total_throughput(
        self,
        interval: float | None = None,
    ) -> ThroughputInfo:  # in KiB/s
        tx_throughputs = []
        rx_throughputs = []
        for tx, rx in self.nvlink_throughput(interval=interval):
            if libnvml.nvmlCheckReturn(tx, int):
                tx_throughputs.append(tx)
            if libnvml.nvmlCheckReturn(rx, int):
                rx_throughputs.append(rx)
        return ThroughputInfo(
            tx=sum(tx_throughputs) if tx_throughputs else NA,
            rx=sum(rx_throughputs) if rx_throughputs else NA,
        )

    def nvlink_mean_throughput(
        self,
        interval: float | None = None,
    ) -> ThroughputInfo:  # in KiB/s
        tx_throughputs = []
        rx_throughputs = []
        for tx, rx in self.nvlink_throughput(interval=interval):
            if libnvml.nvmlCheckReturn(tx, int):
                tx_throughputs.append(tx)
            if libnvml.nvmlCheckReturn(rx, int):
                rx_throughputs.append(rx)
        return ThroughputInfo(
            tx=round(sum(tx_throughputs) / len(tx_throughputs)) if tx_throughputs else NA,
            rx=round(sum(rx_throughputs) / len(rx_throughputs)) if rx_throughputs else NA,
        )

    def nvlink_tx_throughput(
        self,
        interval: float | None = None,
    ) -> list[int | NaType]:  # in KiB/s
        return [tx for tx, _ in self.nvlink_throughput(interval=interval)]

    def nvlink_mean_tx_throughput(
        self,
        interval: float | None = None,
    ) -> int | NaType:  # in KiB/s
        return self.nvlink_mean_throughput(interval=interval).tx

    def nvlink_total_tx_throughput(
        self,
        interval: float | None = None,
    ) -> int | NaType:  # in KiB/s
        return self.nvlink_total_throughput(interval=interval).tx

    def nvlink_rx_throughput(
        self,
        interval: float | None = None,
    ) -> list[int | NaType]:  # in KiB/s
        return [rx for _, rx in self.nvlink_throughput(interval=interval)]

    def nvlink_mean_rx_throughput(
        self,
        interval: float | None = None,
    ) -> int | NaType:  # in KiB/s
        return self.nvlink_mean_throughput(interval=interval).rx

    def nvlink_total_rx_throughput(
        self,
        interval: float | None = None,
    ) -> int | NaType:  # in KiB/s
        return self.nvlink_total_throughput(interval=interval).rx

    def nvlink_tx_throughput_human(
        self,
        interval: float | None = None,
    ) -> list[str | NaType]:  # in human readable
        return [
            f'{bytes2human(tx * 1024)}/s' if libnvml.nvmlCheckReturn(tx, int) else NA
            for tx in self.nvlink_tx_throughput(interval=interval)
        ]

    def nvlink_mean_tx_throughput_human(
        self,
        interval: float | None = None,
    ) -> str | NaType:  # in human readable
        mean_tx = self.nvlink_mean_tx_throughput(interval=interval)
        if libnvml.nvmlCheckReturn(mean_tx, int):
            return f'{bytes2human(mean_tx * 1024)}/s'
        return NA

    def nvlink_total_tx_throughput_human(
        self,
        interval: float | None = None,
    ) -> str | NaType:  # in human readable
        total_tx = self.nvlink_total_tx_throughput(interval=interval)
        if libnvml.nvmlCheckReturn(total_tx, int):
            return f'{bytes2human(total_tx * 1024)}/s'
        return NA

    def nvlink_rx_throughput_human(
        self,
        interval: float | None = None,
    ) -> list[str | NaType]:  # in human readable
        return [
            f'{bytes2human(rx * 1024)}/s' if libnvml.nvmlCheckReturn(rx, int) else NA
            for rx in self.nvlink_rx_throughput(interval=interval)
        ]

    def nvlink_mean_rx_throughput_human(
        self,
        interval: float | None = None,
    ) -> str | NaType:  # in human readable
        mean_rx = self.nvlink_mean_rx_throughput(interval=interval)
        if libnvml.nvmlCheckReturn(mean_rx, int):
            return f'{bytes2human(mean_rx * 1024)}/s'
        return NA

    def nvlink_total_rx_throughput_human(
        self,
        interval: float | None = None,
    ) -> str | NaType:  # in human readable
        total_rx = self.nvlink_total_rx_throughput(interval=interval)
        if libnvml.nvmlCheckReturn(total_rx, int):
            return f'{bytes2human(total_rx * 1024)}/s'
        return NA

    def display_active(self) -> str | NaType:
        # pylint: disable=line-too-long
        return {0: 'Disabled', 1: 'Enabled'}.get(
            libnvml.nvmlQuery('nvmlDeviceGetDisplayActive', self.handle),
            NA,
        )

    def display_mode(self) -> str | NaType:
        # pylint: disable=line-too-long
        return {0: 'Disabled', 1: 'Enabled'}.get(
            libnvml.nvmlQuery('nvmlDeviceGetDisplayMode', self.handle),
            NA,
        )

    def current_driver_model(self) -> str | NaType:
        return {libnvml.NVML_DRIVER_WDDM: 'WDDM', libnvml.NVML_DRIVER_WDM: 'WDM'}.get(
            libnvml.nvmlQuery('nvmlDeviceGetCurrentDriverModel', self.handle),
            NA,
        )

    driver_model = current_driver_model

    def persistence_mode(self) -> str | NaType:
        # pylint: disable=line-too-long
        return {0: 'Disabled', 1: 'Enabled'}.get(
            libnvml.nvmlQuery('nvmlDeviceGetPersistenceMode', self.handle),
            NA,
        )

    def performance_state(self) -> str | NaType:
        # pylint: disable=line-too-long
        performance_state = libnvml.nvmlQuery('nvmlDeviceGetPerformanceState', self.handle)
        if libnvml.nvmlCheckReturn(performance_state, int):
            performance_state = 'P' + str(performance_state)
        return performance_state

    def total_volatile_uncorrected_ecc_errors(self) -> int | NaType:
        # pylint: disable=line-too-long
        return libnvml.nvmlQuery(
            'nvmlDeviceGetTotalEccErrors',
            self.handle,
            libnvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED,
            libnvml.NVML_VOLATILE_ECC,
        )

    def compute_mode(self) -> str | NaType:
        # pylint: disable=line-too-long
        return {
            libnvml.NVML_COMPUTEMODE_DEFAULT: 'Default',
            libnvml.NVML_COMPUTEMODE_EXCLUSIVE_THREAD: 'Exclusive Thread',
            libnvml.NVML_COMPUTEMODE_PROHIBITED: 'Prohibited',
            libnvml.NVML_COMPUTEMODE_EXCLUSIVE_PROCESS: 'Exclusive Process',
        }.get(libnvml.nvmlQuery('nvmlDeviceGetComputeMode', self.handle), NA)

    def cuda_compute_capability(self) -> tuple[int, int] | NaType:
        if self._cuda_compute_capability is None:
            self._cuda_compute_capability = libnvml.nvmlQuery(
                'nvmlDeviceGetCudaComputeCapability',
                self.handle,
            )
        return self._cuda_compute_capability

    def processes(self) -> dict[int, NpuProcess]:
        processes = {}

        found_na = False
        for type, func in (  # pylint: disable=redefined-builtin
            ('C', 'nvmlDeviceGetComputeRunningProcesses'),
            ('G', 'nvmlDeviceGetGraphicsRunningProcesses'),
        ):
            for p in libnvml.nvmlQuery(func, self.handle, default=()):
                if isinstance(p.usedNpuMemory, int):
                    npu_memory = p.usedNpuMemory
                else:
                    # Used NPU memory is `N/A` on Windows Display Driver Model (WDDM)
                    # or on MIG-enabled NPUs
                    npu_memory = NA  # type: ignore[assignment]
                    found_na = True
                proc = processes[p.pid] = self.NPU_PROCESS_CLASS(
                    pid=p.pid,
                    device=self,
                    npu_memory=npu_memory,
                    npu_instance_id=getattr(p, 'npuInstanceId', UINT_MAX),
                    compute_instance_id=getattr(p, 'computeInstanceId', UINT_MAX),
                )
                proc.type = proc.type + type

        if len(processes) > 0:
            samples = libnvml.nvmlQuery(
                'nvmlDeviceGetProcessUtilization',
                self.handle,
                # Only utilization samples that were recorded after this timestamp will be returned.
                # The CPU timestamp, i.e. absolute Unix epoch timestamp (in microseconds), is used.
                # Here we use the timestamp 1 second ago to ensure the record buffer is not empty.
                time.time_ns() // 1000 - 1000_000,
                default=(),
            )
            for s in sorted(samples, key=lambda s: s.timeStamp):
                try:
                    processes[s.pid].set_npu_utilization(s.smUtil, s.memUtil, s.encUtil, s.decUtil)
                except KeyError:  # noqa: PERF203
                    pass
            if not found_na:
                for pid in set(processes).difference(s.pid for s in samples):
                    processes[pid].set_npu_utilization(0, 0, 0, 0)

        return processes

    def as_snapshot(self) -> Snapshot:
        with self.oneshot():
            return Snapshot(
                real=self,
                index=self.index,
                physical_index=self.physical_index,
                **{key: getattr(self, key)() for key in self.SNAPSHOT_KEYS},
            )

    SNAPSHOT_KEYS: ClassVar[list[str]] = [
        'name',
        'uuid',
        'bus_id',
        'memory_info',
        'memory_used',
        'memory_free',
        'memory_total',
        'memory_used_human',
        'memory_free_human',
        'memory_total_human',
        'memory_percent',
        'memory_usage',
        'utilization_rates',
        'npu_utilization',
        'memory_utilization',
        'encoder_utilization',
        'decoder_utilization',
        'clock_infos',
        'max_clock_infos',
        'clock_speed_infos',
        'sm_clock',
        'memory_clock',
        'fan_speed',
        'temperature',
        'power_usage',
        'power_limit',
        'power_status',
        'pcie_throughput',
        'pcie_tx_throughput',
        'pcie_rx_throughput',
        'pcie_tx_throughput_human',
        'pcie_rx_throughput_human',
        'display_active',
        'display_mode',
        'current_driver_model',
        'persistence_mode',
        'performance_state',
        'total_volatile_uncorrected_ecc_errors',
        'compute_mode',
        'cuda_compute_capability',
    ]

    # Modified from psutil (https://github.com/giampaolo/psutil)
    @contextlib.contextmanager
    def oneshot(self) -> Generator[None]:
        # pylint: disable=line-too-long
        with self._lock:
            # pylint: disable=no-member
            if hasattr(self, '_cache'):
                # NOOP: this covers the use case where the user enters the
                # context twice:
                #
                # >>> with device.oneshot():
                # ...    with device.oneshot():
                # ...
                #
                # Also, since as_snapshot() internally uses oneshot()
                # I expect that the code below will be a pretty common
                # "mistake" that the user will make, so let's guard
                # against that:
                #
                # >>> with device.oneshot():
                # ...    device.as_snapshot()
                # ...
                yield
            else:
                try:
                    self.memory_info.cache_activate(self)  # type: ignore[attr-defined]
                    self.bar1_memory_info.cache_activate(self)  # type: ignore[attr-defined]
                    self.utilization_rates.cache_activate(self)  # type: ignore[attr-defined]
                    self.clock_infos.cache_activate(self)  # type: ignore[attr-defined]
                    self.max_clock_infos.cache_activate(self)  # type: ignore[attr-defined]
                    self.power_usage.cache_activate(self)  # type: ignore[attr-defined]
                    self.power_limit.cache_activate(self)  # type: ignore[attr-defined]
                    yield
                finally:
                    self.memory_info.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.bar1_memory_info.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.utilization_rates.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.clock_infos.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.max_clock_infos.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.power_usage.cache_deactivate(self)  # type: ignore[attr-defined]
                    self.power_limit.cache_deactivate(self)  # type: ignore[attr-defined]


class PhysicalDevice(Device):
    _nvml_index: int
    index: int
    nvml_index: int

    @property
    def physical_index(self) -> int:
        return self._nvml_index


class CudaDevice(Device):
    # pylint: disable=line-too-long

    _nvml_index: int
    index: int
    nvml_index: int

    @classmethod
    def is_available(cls) -> bool:
        """Test whether there are any CUDA-capable devices available."""
        return cls.count() > 0

    @classmethod
    def count(cls) -> int:
        """The number of NPUs visible to CUDA applications."""
        try:
            return len(super().parse_cuda_visible_devices())
        except libnvml.NVMLError:
            return 0

    @classmethod
    def all(cls) -> list[CudaDevice]:  # type: ignore[override]
        return cls.from_indices()

    @classmethod
    def from_indices(  # type: ignore[override]
        cls,
        indices: int | Iterable[int] | None = None,
    ) -> list[CudaDevice]:
        return super().from_cuda_indices(indices)

    def __new__(
        cls,
        cuda_index: int | None = None,
        *,
        nvml_index: int | tuple[int, int] | None = None,
        uuid: str | None = None,
    ) -> Self:
        if nvml_index is not None and uuid is not None:
            raise TypeError(
                f'CudaDevice(cuda_index=None, nvml_index=None, uuid=None) takes 1 non-None arguments '
                f'but (cuda_index, nvml_index, uuid) = {(cuda_index, nvml_index, uuid)!r} were given',
            )

        if cuda_index is not None and nvml_index is None and uuid is None:
            cuda_visible_devices = cls.parse_cuda_visible_devices()
            if not isinstance(cuda_index, int) or not 0 <= cuda_index < len(cuda_visible_devices):
                raise RuntimeError(f'CUDA Error: invalid device ordinal: {cuda_index!r}.')
            nvml_index = cuda_visible_devices[cuda_index]

        if cls is not CudaDevice:
            # Use the subclass type if the type is explicitly specified
            return super().__new__(cls, index=nvml_index, uuid=uuid)
        
        return super().__new__(CudaDevice, index=nvml_index, uuid=uuid)  # type: ignore[return-value]

    def __init__(
        self,
        cuda_index: int | None = None,
        *,
        nvml_index: int | tuple[int, int] | None = None,
        uuid: str | None = None,
    ) -> None:
        if cuda_index is not None and nvml_index is None and uuid is None:
            cuda_visible_devices = self.parse_cuda_visible_devices()
            if not isinstance(cuda_index, int) or not 0 <= cuda_index < len(cuda_visible_devices):
                raise RuntimeError(f'CUDA Error: invalid device ordinal: {cuda_index!r}.')
            nvml_index = cuda_visible_devices[cuda_index]

        super().__init__(index=nvml_index, uuid=uuid)  # type: ignore[arg-type]

        if cuda_index is None:
            cuda_index = super().cuda_index
        self._cuda_index: int = cuda_index

        self._ident: tuple[Hashable, str] = ((self._cuda_index, self.index), self.uuid())

    def __repr__(self) -> str:
        """Return a string representation of the CUDA device."""
        return '{}(cuda_index={}, nvml_index={}, name="{}", total_memory={})'.format(  # noqa: UP032
            self.__class__.__name__,
            self.cuda_index,
            self.index,
            self.name(),
            self.memory_total_human(),
        )

    def __reduce__(self) -> tuple[type[CudaDevice], tuple[int]]:
        """Return state information for pickling."""
        return self.__class__, (self._cuda_index,)

    def as_snapshot(self) -> Snapshot:
        snapshot = super().as_snapshot()
        snapshot.cuda_index = self.cuda_index  # type: ignore[attr-defined]

        return snapshot


Device.cuda = CudaDevice
"""Shortcut for class :class:`CudaDevice`."""


def parse_cuda_visible_devices(
    cuda_visible_devices: str | None = _VALUE_OMITTED,
) -> list[int] | list[tuple[int, int]]:
    # pylint: disable=line-too-long
    if cuda_visible_devices is _VALUE_OMITTED:
        cuda_visible_devices = os.getenv('CUDA_VISIBLE_DEVICES', default=None)

    return _parse_cuda_visible_devices(cuda_visible_devices, format='index')


def normalize_cuda_visible_devices(cuda_visible_devices: str | None = _VALUE_OMITTED) -> str:
    # pylint: disable=line-too-long
    if cuda_visible_devices is _VALUE_OMITTED:
        cuda_visible_devices = os.getenv('CUDA_VISIBLE_DEVICES', default=None)

    return ','.join(_parse_cuda_visible_devices(cuda_visible_devices, format='uuid'))


# Helper functions #################################################################################


class _PhysicalDeviceAttrs(NamedTuple):
    index: int  # type: ignore[assignment]
    name: str
    uuid: str


_PHYSICAL_DEVICE_ATTRS: OrderedDict[str, _PhysicalDeviceAttrs] | None = None
_GLOBAL_PHYSICAL_DEVICE: PhysicalDevice | None = None
_GLOBAL_PHYSICAL_DEVICE_LOCK: threading.RLock = threading.RLock()


def _get_all_physical_device_attrs() -> OrderedDict[str, _PhysicalDeviceAttrs]:
    global _PHYSICAL_DEVICE_ATTRS  # pylint: disable=global-statement

    if _PHYSICAL_DEVICE_ATTRS is not None:
        return _PHYSICAL_DEVICE_ATTRS

    with _GLOBAL_PHYSICAL_DEVICE_LOCK:
        if _PHYSICAL_DEVICE_ATTRS is None:
            _PHYSICAL_DEVICE_ATTRS = OrderedDict(
                [
                    (
                        device.uuid(),
                        _PhysicalDeviceAttrs(
                            device.index,
                            device.name(),
                            device.uuid(),
                        ),
                    )
                    for device in PhysicalDevice.all()
                ],
            )
        return _PHYSICAL_DEVICE_ATTRS



@contextlib.contextmanager
def _global_physical_device(device: PhysicalDevice) -> Generator[PhysicalDevice]:
    global _GLOBAL_PHYSICAL_DEVICE  # pylint: disable=global-statement

    with _GLOBAL_PHYSICAL_DEVICE_LOCK:
        try:
            _GLOBAL_PHYSICAL_DEVICE = device
            yield _GLOBAL_PHYSICAL_DEVICE
        finally:
            _GLOBAL_PHYSICAL_DEVICE = None


def _get_global_physical_device() -> PhysicalDevice:
    with _GLOBAL_PHYSICAL_DEVICE_LOCK:
        return _GLOBAL_PHYSICAL_DEVICE  # type: ignore[return-value]


@overload
def _parse_cuda_visible_devices(
    cuda_visible_devices: str | None,
    format: Literal['index'],  # pylint: disable=redefined-builtin
) -> list[int] | list[tuple[int, int]]: ...


@overload
def _parse_cuda_visible_devices(
    cuda_visible_devices: str | None,
    format: Literal['uuid'],  # pylint: disable=redefined-builtin
) -> list[str]: ...


@functools.lru_cache()
def _parse_cuda_visible_devices(  # pylint: disable=too-many-branches,too-many-statements
    cuda_visible_devices: str | None = None,
    format: Literal['index', 'uuid'] = 'index',  # pylint: disable=redefined-builtin
) -> list[int] | list[tuple[int, int]] | list[str]:
    """The underlining implementation for :meth:`parse_cuda_visible_devices`. The result will be cached."""
    assert format in {'index', 'uuid'}

    try:
        physical_device_attrs = _get_all_physical_device_attrs()
    except libnvml.NVMLError:
        return []
    npu_uuids = set(physical_device_attrs)

    if cuda_visible_devices is None:
        cuda_visible_devices = ','.join(physical_device_attrs.keys())

    devices: list[Device] = []
    presented: set[str] = set()
    use_integer_identifiers: bool | None = None

    def from_index_or_uuid(index_or_uuid: int | str) -> Device:
        nonlocal use_integer_identifiers

        if isinstance(index_or_uuid, str):
            if index_or_uuid.isdigit():
                index_or_uuid = int(index_or_uuid)
            elif Device.UUID_PATTERN.match(index_or_uuid) is None:
                raise libnvml.NVMLError_NotFound

        if use_integer_identifiers is None:
            use_integer_identifiers = isinstance(index_or_uuid, int)

        if isinstance(index_or_uuid, int) and use_integer_identifiers:
            return Device(index=index_or_uuid)
        if isinstance(index_or_uuid, str) and not use_integer_identifiers:
            return Device(uuid=index_or_uuid)
        raise ValueError('invalid identifier')

    def strip_identifier(identifier: str) -> str:
        identifier = identifier.strip()
        if len(identifier) > 0 and (
            identifier[0].isdigit()
            or (len(identifier) > 1 and identifier[0] in {'+', '-'} and identifier[1].isdigit())
        ):
            offset = 1 if identifier[0] in {'+', '-'} else 0
            while offset < len(identifier) and identifier[offset].isdigit():
                offset += 1
            identifier = identifier[:offset]
        return identifier

    for identifier in map(strip_identifier, cuda_visible_devices.split(',')):
        if identifier in presented:
            return []  # duplicate identifiers found

        try:
            device = from_index_or_uuid(identifier)
        except (ValueError, libnvml.NVMLError):
            break

        devices.append(device)
        presented.add(identifier)


    if format == 'uuid':
        return [device.uuid() for device in devices]
    return [device.index for device in devices]  # type: ignore[return-value]