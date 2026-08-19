# This file is part of nputop, the interactive Ascend-NPU process viewer.
# License: GNU GPL version 3.


# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

from cachetools.func import ttl_cache

from nputop.api import NA
from nputop.api import MigDevice as MigDeviceBase
from nputop.api import PhysicalDevice as DeviceBase
from nputop.api import utilization2string
from nputop.gui.library.process import NpuProcess
from nputop.gui.library.utils import cut_string


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
        'max_sm_clock',
        'hbm_frequency',
        'hbm_temperature',
        'hbm_bandwidth',
        'memory_bandwidth_utilization',
        'aicpu_utilization',
        'encoder_utilization',
        'decoder_utilization',
        'pcie_tx_throughput_human',
        'pcie_rx_throughput_human',
        'dcmi_aicore_pcie_summary',
        'dcmi_bus_hbm_summary',
        'dcmi_power_hbm_summary',
        'dcmi_npu_aux_summary',
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

    def max_sm_clock(self):
        return self.max_clock_infos().sm

    @staticmethod
    def _metric(value, suffix=''):
        return f'{value}{suffix}' if value != NA else NA

    @staticmethod
    def _short_rate(value):
        if value == NA:
            return NA
        value = f'{value / (1024 * 1000):.1f}'
        return value[:-2] if value.endswith('.0') else value

    def dcmi_aicore_pcie_summary(self):
        name = cut_string(self.name(), maxlen=10, padstr='..', align='right')
        current = self._metric(self.sm_clock())
        maximum = self._metric(self.max_sm_clock())
        tx = self._short_rate(self.pcie_tx_throughput())
        rx = self._short_rate(self.pcie_rx_throughput())
        pcie = f'{tx}/{rx}G' if tx != NA and rx != NA else f'{tx}/{rx}'
        return cut_string(
            f'{self.physical_index} {name} A{current}/{maximum} P{pcie}',
            29,
            padstr='..',
        )

    def dcmi_bus_hbm_summary(self):
        bus_id = self.bus_id()
        if bus_id != NA and len(bus_id) > 10 and ':' in bus_id:
            bus_id = bus_id.split(':', 1)[1]
        frequency = self._metric(self.memory_clock())
        return cut_string(f'{bus_id} H{frequency}', 20, padstr='..')

    def dcmi_power_hbm_summary(self):
        fan = self.fan_speed_string()
        temperature = self.temperature_string()
        power_usage = self.power_usage()
        power_limit = self.power_limit()
        if isinstance(power_usage, int):
            power = f'P{power_usage / 1000:.0f}W'
            if isinstance(power_limit, int):
                power += f'/{power_limit:.0f}W'
        else:
            power = 'P' + NA
        hbm_temperature = self._metric(self.hbm_temperature())
        hbm_bandwidth = self._metric(self.hbm_bandwidth())
        return cut_string(
            f'{fan} {temperature} {power} H{hbm_temperature}/B{hbm_bandwidth}',
            29,
            padstr='..',
        )

    def dcmi_npu_aux_summary(self):
        npu = self._metric(self.npu_utilization(), '%')
        aicpu = self._metric(self.aicpu_utilization(), '%')
        decoder = self._metric(self.decoder_utilization())
        encoder = self._metric(self.encoder_utilization())
        return cut_string(f'N{npu} C{aicpu} D{decoder}/E{encoder}', 20, padstr='..')

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
        if utilization == NA:
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
