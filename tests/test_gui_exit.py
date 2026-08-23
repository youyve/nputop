import threading
import time
from unittest.mock import Mock

from nputop.gui.ui import UI
from nputop.gui.screens.main.device import DevicePanel
from nputop.gui.screens.main.host import HostPanel
from nputop.gui.screens.main import BreakLoop
from nputop.gui.screens.main.process import ProcessPanel


class Root:
    width = 79


class SlowDevice:
    def __init__(self):
        self.snapshot_started = threading.Event()

    def mig_devices(self):
        return []

    def as_snapshot(self):
        self.snapshot_started.set()
        time.sleep(0.2)
        raise AssertionError('snapshot collection should be asynchronous in this test')


def test_ui_print_uses_cached_snapshots_after_monitor_exit():
    ui = object.__new__(UI)
    ui.main_screen = Mock()

    ui.print(refresh=False)

    ui.main_screen.print.assert_called_once_with(refresh=False)


def test_ui_print_refreshes_for_non_interactive_output():
    ui = object.__new__(UI)
    ui.main_screen = Mock()

    ui.print(refresh=True)

    ui.main_screen.print.assert_called_once_with(refresh=True)


def test_process_panel_cached_print_does_not_collect_again(monkeypatch, capsys):
    panel = ProcessPanel([], compact=True, filters=[], win=None, root=Root())
    collect = Mock(side_effect=AssertionError('unexpected synchronous collection'))
    monkeypatch.setattr(panel, 'ensure_snapshots', collect)

    panel.print(refresh=False)

    collect.assert_not_called()
    assert 'Gathering process status' in capsys.readouterr().out


def test_device_panel_initialization_does_not_collect_device_snapshots():
    device = SlowDevice()

    panel = DevicePanel([device], compact=True, win=None, root=Root())

    assert panel.snapshots == []
    assert not device.snapshot_started.is_set()


def test_device_panel_poke_only_starts_background_collection():
    panel = DevicePanel([], compact=True, win=None, root=Root())
    panel._snapshot_daemon = Mock()  # pylint: disable=protected-access
    panel.take_snapshots = Mock(side_effect=AssertionError('unexpected synchronous collection'))

    panel.poke()

    panel.take_snapshots.assert_not_called()
    panel._snapshot_daemon.start.assert_called_once()  # pylint: disable=protected-access
    panel.destroy()


def test_host_panel_poke_only_starts_background_collection():
    panel = HostPanel([], compact=True, win=None, root=Root())
    panel._snapshot_daemon = Mock()  # pylint: disable=protected-access
    panel.take_snapshots = Mock(side_effect=AssertionError('unexpected synchronous collection'))

    panel.poke()

    panel.take_snapshots.assert_not_called()
    panel._snapshot_daemon.start.assert_called_once()  # pylint: disable=protected-access
    panel.destroy()


def test_ui_loop_processes_queued_quit_before_redraw():
    ui = object.__new__(UI)
    ui.win = Mock()
    ui.handle_input = Mock(side_effect=BreakLoop)
    ui.redraw = Mock()

    ui.loop()

    ui.redraw.assert_not_called()
