from types import SimpleNamespace
from unittest.mock import Mock

from nputop.api import libascend, libdcmi
from nputop.api.utils import NA
from nputop.gui.library.device import Device
from nputop.gui.screens.main.host import HostPanel
from nputop.gui.screens.main import MainScreen
from test_libdcmi import FakeDcmiLibrary


def test_first_frame_shows_loading_instead_of_empty_tables():
    screen = SimpleNamespace(
        color_reset=Mock(), addstr=Mock(), _waiting_for_devices=True,
        root=SimpleNamespace(x=0, y=0),
    )
    MainScreen.draw(screen)
    screen.addstr.assert_called_once_with(
        0, 0, 'nputop: Collecting device metrics... (q to quit)',
    )


def test_compact_snapshot_skips_detail_queries_and_can_expand(monkeypatch):
    library = FakeDcmiLibrary()
    backend = libdcmi.create_backend(library)
    monkeypatch.setattr(libascend, '_DCMI_BACKEND', backend)
    monkeypatch.setattr(libascend, '_DCMI_ATTEMPTED', True)
    device = Device(0)
    details = ('pcie_throughput', 'max_clock_infos', 'clock_infos',
               'ecc_info', 'dvpp_utilization')
    spies = {}
    for name in details:
        spy = Mock(wraps=getattr(backend, name))
        monkeypatch.setattr(backend, name, spy)
        spies[name] = spy

    compact = device.as_snapshot(compact=True)

    assert compact.memory_total == 64 * 1024**3
    assert compact.npu_utilization == 63
    assert compact.power_usage == 123400
    assert compact.pcie_tx_throughput_human == NA
    assert compact.aicore_pcie_summary == NA
    for spy in spies.values():
        spy.assert_not_called()

    full = device.as_snapshot()
    assert full.pcie_tx_throughput_human != NA
    assert full.aicore_pcie_summary != NA
    for spy in spies.values():
        assert spy.called
    assert device.cached_snapshot is full


def test_cached_snapshot_does_not_start_collection(monkeypatch):
    device = Device(0)
    collect = Mock(side_effect=AssertionError('unexpected device I/O'))
    monkeypatch.setattr(device, 'as_snapshot', collect)
    assert device.cached_snapshot is None
    collect.assert_not_called()


def test_host_aggregation_waits_for_cached_device_sample(monkeypatch):
    from nputop.gui.screens.main import host as host_module

    class DeviceWithoutSnapshot:
        cached_snapshot = None

        @property
        def snapshot(self):
            raise AssertionError('host panel must not initiate device I/O')

    panel = object.__new__(HostPanel)
    panel.devices = [DeviceWithoutSnapshot()]
    panel.average_npu_memory_percent = Mock()
    panel.average_npu_utilization = Mock()
    for name in ('cpu_percent', 'virtual_memory', 'swap_memory'):
        metric = Mock()
        metric.history = SimpleNamespace(last_value=0, last_retval=0)
        monkeypatch.setattr(host_module.host, name, metric)
    monkeypatch.setattr(host_module.host, 'load_average', lambda: (0, 0, 0))

    panel.take_snapshots()
    panel.average_npu_memory_percent.add.assert_not_called()
    panel.average_npu_utilization.add.assert_not_called()

    panel.devices[0].cached_snapshot = SimpleNamespace(
        memory_used=25, memory_total=100, npu_utilization=63,
    )
    panel.take_snapshots()
    panel.average_npu_memory_percent.add.assert_called_once_with(25.0)
    panel.average_npu_utilization.add.assert_called_once_with(63.0)
