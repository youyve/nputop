from unittest.mock import Mock

from nputop.gui.ui import UI
from nputop.gui.screens.main.process import ProcessPanel


class Root:
    width = 79


def test_ui_print_uses_cached_snapshots_after_monitor_exit():
    ui = object.__new__(UI)
    ui.win = object()
    ui.main_screen = Mock()

    ui.print()

    ui.main_screen.print.assert_called_once_with(refresh=False)


def test_process_panel_cached_print_does_not_collect_again(monkeypatch, capsys):
    panel = ProcessPanel([], compact=True, filters=[], win=None, root=Root())
    collect = Mock(side_effect=AssertionError('unexpected synchronous collection'))
    monkeypatch.setattr(panel, 'ensure_snapshots', collect)

    panel.print(refresh=False)

    collect.assert_not_called()
    assert 'Gathering process status' in capsys.readouterr().out
