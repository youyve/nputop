# This file is part of nputop, the interactive Ascend-NPU process viewer.
# License: GNU GPL version 3.


# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

from cachetools.func import ttl_cache

from nputop.api import NA
from nputop.api import MigDevice as MigDeviceBase
from nputop.api import PhysicalDevice as DeviceBase
from nputop.api import utilization2string
from nputop.gui.library.process import NpuProcess


__all__ = ['Device', 'NA']


class Device(DeviceBase):
    NPU_PROCESS_CLASS = NpuProcess

    MEMORY_UTILIZATION_THRESHOLDS = (10, 80)
    NPU_UTILIZATION_THRESHOLDS = (10, 75)
    INTENSITY2COLOR = {'light': 'green', 'moderate': 'yellow', 'heavy': 'red'}

    SNAPSHOT_KEYS = [
        'name',
        'bus_id',
        'memory_used',
        'memory_free',
        'memory_total',
        'memory_used_human',
        'memory_free_human',
        'memory_total_human',
        'memory_percent',
        'memory_usage',
        'npu_utilization',
        'memory_utilization',
        'fan_speed',
        'temperature',
        'power_usage',
        'power_limit',
        'power_status',
        'display_active',
        'current_driver_model',
        'persistence_mode',
        'performance_state',
        'total_volatile_uncorrected_ecc_errors',
        'compute_mode',
        'mig_mode',
        'is_mig_device',
        'memory_percent_string',
        'memory_utilization_string',
        'npu_utilization_string',
        'fan_speed_string',
        'temperature_string',
        'memory_loading_intensity',
        'memory_display_color',
        'npu_loading_intensity',
        'npu_display_color',
        'loading_intensity',
        'display_color',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._snapshot = None
        self.tuple_index = (self.index,) if isinstance(self.index, int) else self.index
        self.display_index = ':'.join(map(str, self.tuple_index))

    def as_snapshot(self):
        self._snapshot = super().as_snapshot()
        self._snapshot.tuple_index = self.tuple_index
        self._snapshot.display_index = self.display_index
        return self._snapshot

    @property
    def snapshot(self):
        if self._snapshot is None:
            self.as_snapshot()
        return self._snapshot

    def mig_devices(self):
        mig_devices = []

        if self.is_mig_mode_enabled():
            for mig_index in range(self.max_mig_device_count()):
                try:
                    mig_device = MigDevice(index=(self.index, mig_index))
                except libnvml.NVMLError:  # noqa: PERF203
                    break
                else:
                    mig_devices.append(mig_device)

        return mig_devices

    fan_speed = ttl_cache(ttl=5.0)(DeviceBase.fan_speed)
    temperature = ttl_cache(ttl=5.0)(DeviceBase.temperature)
    power_usage = ttl_cache(ttl=5.0)(DeviceBase.power_usage)
    display_active = ttl_cache(ttl=5.0)(DeviceBase.display_active)
    display_mode = ttl_cache(ttl=5.0)(DeviceBase.display_mode)
    current_driver_model = ttl_cache(ttl=5.0)(DeviceBase.current_driver_model)
    persistence_mode = ttl_cache(ttl=5.0)(DeviceBase.persistence_mode)
    performance_state = ttl_cache(ttl=5.0)(DeviceBase.performance_state)
    total_volatile_uncorrected_ecc_errors = ttl_cache(ttl=5.0)(
        DeviceBase.total_volatile_uncorrected_ecc_errors,
    )
    compute_mode = ttl_cache(ttl=5.0)(DeviceBase.compute_mode)
    mig_mode = ttl_cache(ttl=5.0)(DeviceBase.mig_mode)

    def memory_percent_string(self):  # in percentage
        return utilization2string(self.memory_percent())

    def memory_utilization_string(self):  # in percentage
        return utilization2string(self.memory_utilization())

    def npu_utilization_string(self):  # in percentage
        return utilization2string(self.npu_utilization())

    def fan_speed_string(self):  # in percentage
        return utilization2string(self.fan_speed())

    def temperature_string(self):  # in Celsius
        return self.temperature()

    def memory_loading_intensity(self):
        return self.loading_intensity_of(self.memory_percent(), type='memory')

    def npu_loading_intensity(self):
        return self.loading_intensity_of(self.npu_utilization(), type='npu')

    def loading_intensity(self):
        loading_intensity = (self.memory_loading_intensity(), self.npu_loading_intensity())
        if 'heavy' in loading_intensity:
            return 'heavy'
        if 'moderate' in loading_intensity:
            return 'moderate'
        return 'light'

    def display_color(self):
        if self.name().startswith('ERROR:'):
            return 'red'
        return self.INTENSITY2COLOR.get(self.loading_intensity())

    def memory_display_color(self):
        if self.name().startswith('ERROR:'):
            return 'red'
        return self.INTENSITY2COLOR.get(self.memory_loading_intensity())

    def npu_display_color(self):
        if self.name().startswith('ERROR:'):
            return 'red'
        return self.INTENSITY2COLOR.get(self.npu_loading_intensity())

    @staticmethod
    def loading_intensity_of(utilization, type='memory'):  # pylint: disable=redefined-builtin
        thresholds = {
            'memory': Device.MEMORY_UTILIZATION_THRESHOLDS,
            'npu': Device.NPU_UTILIZATION_THRESHOLDS,
        }.get(type)
        if utilization is NA:
            return 'moderate'
        if isinstance(utilization, str):
            utilization = utilization.replace('%', '')
        utilization = float(utilization)
        if utilization >= thresholds[-1]:
            return 'heavy'
        if utilization >= thresholds[0]:
            return 'moderate'
        return 'light'

    @staticmethod
    def color_of(utilization, type='memory'):  # pylint: disable=redefined-builtin
        return Device.INTENSITY2COLOR.get(Device.loading_intensity_of(utilization, type=type))



