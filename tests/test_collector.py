"""Exercise public collection APIs with Ascend telemetry and a real host process."""

import math
import os

import pytest

from nputop import Device, ResourceMetricCollector, take_snapshots
from nputop.api import libascend, libdcmi
from nputop.api.utils import NA
from test_libdcmi import FakeDcmiLibrary


@pytest.fixture
def ascend_backend(monkeypatch):
    class Library(FakeDcmiLibrary):
        def dcmi_get_device_resource_info(self, card, chip, entries, count):
            entries[0].proc_id = os.getpid()
            entries[0].proc_mem_usage = 4096
            count._obj.value = 1
            return 0

    backend = libdcmi.create_backend(Library())
    monkeypatch.setattr(libascend.libdcmi, 'create_backend', lambda: backend)
    libascend._reset_dcmi_backend()
    yield backend
    libascend._reset_dcmi_backend()


def test_public_snapshots_enumerate_ascend_devices_and_processes(ascend_backend):
    snapshots = take_snapshots()
    assert len(snapshots.devices) == ascend_backend.count()
    assert [d.index for d in snapshots.devices] == [0, 1, 2]
    assert all(d.real.mig_devices() == [] for d in snapshots.devices)
    assert len(snapshots.npu_processes) == 3
    assert all(p.pid == os.getpid() for p in snapshots.npu_processes)
    assert all(p.npu_memory == 4096 for p in snapshots.npu_processes)


@pytest.mark.parametrize('explicit_devices', [False, True])
@pytest.mark.parametrize('missing', [NA, 'N/A', None])
def test_resource_collector_preserves_units_and_missing_metrics(
    ascend_backend, explicit_devices, missing, monkeypatch,
):
    monkeypatch.setattr(Device, 'fan_speed', lambda self: missing)
    kwargs = {'devices': Device.all()} if explicit_devices else {}
    collector = ResourceMetricCollector(interval=0.05, **kwargs)
    try:
        with collector.context('test'):
            metrics = collector.collect()
            assert metrics['test/npu:0/power_usage (W)/mean'] == pytest.approx(123.4)
            assert metrics['test/npu:0/memory_total (MiB)/mean'] == 64 * 1024
            assert math.isnan(metrics['test/npu:0/fan_speed (%)/mean'])
            prefix = 'test/pid:%s/' % os.getpid()
            assert math.isfinite(metrics[prefix + 'host/host_memory_percent (%)/mean'])
            assert math.isnan(metrics[prefix + 'npu:0/npu_sm_utilization (%)/mean'])
            assert not any('cuda:' in key for key in metrics)
    finally:
        collector._daemon_running.clear()
        collector._daemon.join(timeout=2)
    assert not collector._daemon.is_alive()


def test_collector_invalid_interval_does_not_break_cleanup():
    collector = ResourceMetricCollector.__new__(ResourceMetricCollector)
    with pytest.raises(ValueError, match='interval'):
        collector.__init__(interval=0)
    collector.__del__()
