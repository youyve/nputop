from nputop.api import device as device_module
from nputop.api.device import Device, MemoryInfo
from nputop.api.utils import NA


def test_memory_info_normalizes_failed_query(monkeypatch):
    monkeypatch.setattr(device_module.libnvml, "nvmlQuery", lambda *args, **kwargs: NA)

    device = Device(0)

    assert device.memory_info() == MemoryInfo(total=NA, free=NA, used=NA)
    assert device.memory_total() == NA
    assert device.memory_free() == NA
    assert device.memory_used() == NA
    assert device.memory_percent() == NA


def test_snapshot_survives_failed_memory_query(monkeypatch):
    monkeypatch.setattr(device_module.libnvml, "nvmlQuery", lambda *args, **kwargs: NA)

    snapshot = Device(0).as_snapshot()

    assert snapshot.memory_info == MemoryInfo(total=NA, free=NA, used=NA)
    assert snapshot.memory_used == NA
