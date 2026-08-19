import ctypes

from nputop.api import libascend, libdcmi
from nputop.api.device import Device
from nputop.api.utils import NA
from nputop.gui.library.device import Device as GuiDevice
from nputop.gui.screens.main.device import DevicePanel


class FakeDcmiLibrary:
    """Minimal ctypes-compatible DCMI double used by unit tests."""

    def dcmi_init(self):
        return 0

    def dcmi_get_card_num_list(self, count, cards, _list_len):
        count._obj.value = 2
        cards[0] = 5
        cards[1] = 8
        return 0

    def dcmi_get_device_num_in_card(self, card_id, count):
        count._obj.value = 2 if card_id == 5 else 1
        return 0

    def dcmi_get_device_chip_info_v2(self, _card_id, _chip_id, info):
        info._obj.npu_name = (ctypes.c_ubyte * 32)(*b'Fake910\0')
        return 0

    def dcmi_get_device_pcie_info_v2(self, _card_id, chip_id, info):
        info._obj.domain = 0
        info._obj.bdf_busid = 0x20 + chip_id
        info._obj.bdf_deviceid = 0
        info._obj.bdf_funcid = chip_id
        return 0

    def dcmi_get_device_hbm_info(self, _card_id, chip_id, info):
        info._obj.memory_size = 64 * 1024
        info._obj.memory_usage = 1024 + chip_id
        info._obj.freq = 1600
        info._obj.temp = 42
        info._obj.bandwith_util_rate = 7
        return 0

    def dcmi_get_device_temperature(self, _card_id, _chip_id, value):
        value._obj.value = 42
        return 0

    def dcmi_get_device_power_info(self, _card_id, _chip_id, value):
        value._obj.value = 1234
        return 0

    def dcmi_get_device_frequency(self, _card_id, _chip_id, selector, value):
        values = {
            libdcmi.DCMI_FREQ_AICORE_CURRENT: 800,
            libdcmi.DCMI_FREQ_AICORE_MAX: 1800,
            libdcmi.DCMI_FREQ_HBM: 1600,
            libdcmi.DCMI_FREQ_DDR: 1200,
        }
        value._obj.value = values.get(selector, 0)
        return 0

    def dcmi_get_device_fan_count(self, _card_id, _chip_id, count):
        count._obj.value = 1
        return 0

    def dcmi_get_device_fan_speed(self, _card_id, _chip_id, fan_id, speed):
        assert fan_id == 0
        speed._obj.value = 9000
        return 0

    def dcmi_get_device_dvpp_ratio_info(self, _card_id, _chip_id, ratio):
        ratio._obj.vdec_ratio = 12
        ratio._obj.vpc_ratio = 23
        ratio._obj.venc_ratio = 34
        ratio._obj.jpege_ratio = 45
        ratio._obj.jpegd_ratio = 56
        return 0

    def dcmi_get_device_ecc_info(self, _card_id, _chip_id, _device_type, info):
        info._obj.enable_flag = 1
        info._obj.single_bit_error_cnt = 2
        info._obj.double_bit_error_cnt = 3
        info._obj.total_single_bit_error_cnt = 4
        info._obj.total_double_bit_error_cnt = 5
        info._obj.single_bit_isolated_pages_cnt = 6
        info._obj.double_bit_isolated_pages_cnt = 7
        return 0

    def dcmi_get_pcie_link_bandwidth_info(self, _card_id, _chip_id, info):
        assert info._obj.profiling_time == libdcmi.PCIE_PROFILING_TIME_MS
        info._obj.tx_p_bw[2] = 1
        info._obj.tx_np_bw[2] = 2
        info._obj.tx_cpl_bw[2] = 3
        info._obj.rx_p_bw[2] = 4
        info._obj.rx_np_bw[2] = 5
        info._obj.rx_cpl_bw[2] = 6
        return 0

    def dcmi_get_device_utilization_rate(self, _card_id, _chip_id, selector, value):
        value._obj.value = 63 if selector == libdcmi.DCMI_UTILIZATION_RATE_NPU else 7
        return 0

    def dcmi_get_device_resource_info(self, _card_id, _chip_id, entries, count):
        entries[0].proc_id = 123
        entries[0].proc_mem_usage = 4096
        count._obj.value = 1
        return 0

    def dcmi_get_driver_version(self, value, _length):
        value.value = b'9.1.0'
        return 0


class FailingMemoryDcmiLibrary(FakeDcmiLibrary):
    def dcmi_get_device_hbm_info(self, _card_id, _chip_id, _info):
        return -1

    def dcmi_get_device_memory_info_v3(self, _card_id, _chip_id, _info):
        return -1


def test_dcmi_backend_enumerates_devices_and_converts_units():
    backend = libdcmi.create_backend(FakeDcmiLibrary())

    assert backend.count() == 3
    assert backend.uuid(0) == 'ASCEND-05-00'
    assert backend.uuid(2) == 'ASCEND-08-00'
    assert backend.name(0) == 'Fake910'
    assert backend.bus_id(1) == '0000:21:00.1'
    assert backend.memory_info(0) == libdcmi.MemoryInfo(
        64 * 1024 * 1024 * 1024,
        (64 * 1024 - 1024) * 1024 * 1024,
        1024 * 1024 * 1024,
    )
    assert backend.hbm_info(0) == libdcmi.HbmInfo(
        64 * 1024 * 1024 * 1024,
        1600,
        1024 * 1024 * 1024,
        42,
        7,
    )
    assert backend.temperature(0) == 42
    assert backend.power_usage(0) == 123400
    assert backend.utilization_rates(0).npu == 63
    assert backend.utilization_rates(0).bandwidth == 7
    assert backend.utilization_rates(0).aicpu == 7
    assert backend.fan_speed(0) == 50
    assert backend.clock_infos(0) == libdcmi.ClockInfos('N/A', 800, 1600, 'N/A')
    assert backend.max_clock_infos(0) == libdcmi.ClockInfos('N/A', 1800, 'N/A', 'N/A')
    assert backend.dvpp_utilization(0) == libdcmi.DvppUtilization(12, 34)
    assert backend.total_volatile_uncorrected_ecc_errors(0) == 3
    assert backend.pcie_throughput(0) == libdcmi.ThroughputInfo(
        6 * 1024 * 1000,
        15 * 1024 * 1000,
    )
    assert backend.process_info(0) == (libdcmi.ProcessInfo(123, 4096),)
    assert backend.driver_version() == '9.1.0'


def test_dcmi_field_failure_returns_na_without_fallback():
    backend = libdcmi.create_backend(FailingMemoryDcmiLibrary())

    assert backend.memory_info(0) == libdcmi.MemoryInfo(NA, NA, NA)


def test_libascend_uses_dcmi_backend_and_keeps_compat_types(monkeypatch):
    backend = libdcmi.create_backend(FakeDcmiLibrary())
    monkeypatch.setattr(libascend.libdcmi, 'create_backend', lambda: backend)
    libascend._reset_dcmi_backend()

    assert libascend.ascendDeviceGetCount() == 3
    assert isinstance(libascend.ascendDeviceGetMemoryInfo(0), libascend.MemInfo)
    assert isinstance(libascend.ascendDeviceGetUtilizationRates(0), libascend.Util)
    assert isinstance(libascend.ascendDeviceGetProcessInfo(0)[0], libascend.ProcInfo)

    device = Device(0)
    assert device.uuid() == 'ASCEND-05-00'
    assert device.bus_id() == '0000:20:00.0'
    assert device.memory_used() == 1024 * 1024 * 1024
    assert device.fan_speed() == 50
    assert device.encoder_utilization() == 34
    assert device.decoder_utilization() == 12
    assert device.memory_bandwidth_utilization() == 7
    assert device.aicpu_utilization() == 7
    assert device.hbm_frequency() == 1600
    assert device.hbm_temperature() == 42
    assert device.hbm_bandwidth() == 7
    assert device.sm_clock() == 800
    assert device.memory_clock() == 1600
    assert device.pcie_tx_throughput() == 6 * 1024 * 1000
    assert device.pcie_rx_throughput() == 15 * 1024 * 1000
    assert device.total_volatile_uncorrected_ecc_errors() == 3
    snapshot = device.as_snapshot()
    assert snapshot.fan_speed == 50
    assert snapshot.sm_clock == 800
    assert snapshot.memory_clock == 1600
    assert snapshot.encoder_utilization == 34
    assert snapshot.decoder_utilization == 12
    assert snapshot.total_volatile_uncorrected_ecc_errors == 3

    gui_snapshot = GuiDevice(0).as_snapshot()
    assert gui_snapshot.hbm_frequency == 1600
    assert gui_snapshot.hbm_temperature == 42
    assert gui_snapshot.hbm_bandwidth == 7
    assert gui_snapshot.memory_bandwidth_utilization == 7
    assert gui_snapshot.aicpu_utilization == 7
    assert gui_snapshot.dcmi_aicore_pcie_summary == '0 Fake910 A800/1800 P6/15G'
    assert gui_snapshot.dcmi_bus_hbm_summary == '20:00.0 H1600'
    assert gui_snapshot.dcmi_power_hbm_summary == '50% 42 P123W H42/B7'
    assert gui_snapshot.dcmi_npu_aux_summary == 'N63% C7% D12/E34'

    class Root:
        width = 79

    panel = DevicePanel([GuiDevice(0)], compact=True, win=None, root=Root())
    assert len(panel.formats_compact) == 1
    assert panel.height == 8
    assert all(len(line) == 79 for line in panel.frame_lines())

    full_panel = DevicePanel([GuiDevice(0)], compact=False, win=None, root=Root())
    assert len(full_panel.formats_full) == 2
    assert full_panel.height == 10
    assert all(len(line) == 79 for line in full_panel.frame_lines())

    libascend._reset_dcmi_backend()


def test_libascend_falls_back_to_npusmi_when_dcmi_unavailable(monkeypatch):
    def unavailable():
        raise libdcmi.DcmiUnavailable('test')

    monkeypatch.setattr(libascend.libdcmi, 'create_backend', unavailable)
    monkeypatch.setattr(libascend, '_update_cache', lambda: libascend._IDX.__setitem__(slice(None), [0]))
    libascend._reset_dcmi_backend()

    assert libascend.ascendDeviceGetCount() == 1

    libascend._reset_dcmi_backend()
