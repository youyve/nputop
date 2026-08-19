import ctypes

from nputop.api import libascend, libdcmi
from nputop.api.device import Device
from nputop.api.utils import NA


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
    assert backend.temperature(0) == 42
    assert backend.power_usage(0) == 123400
    assert backend.utilization_rates(0).npu == 63
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

    libascend._reset_dcmi_backend()


def test_libascend_falls_back_to_npusmi_when_dcmi_unavailable(monkeypatch):
    def unavailable():
        raise libdcmi.DcmiUnavailable('test')

    monkeypatch.setattr(libascend.libdcmi, 'create_backend', unavailable)
    monkeypatch.setattr(libascend, '_update_cache', lambda: libascend._IDX.__setitem__(slice(None), [0]))
    libascend._reset_dcmi_backend()

    assert libascend.ascendDeviceGetCount() == 1

    libascend._reset_dcmi_backend()
