"""Observed 310P3 SMI layout: large card ID, DDR, absent power, no host PID proof."""

import ctypes
from types import SimpleNamespace

import pytest

from nputop.api import monitor
from nputop.gui.library.widestring import WideString
from nputop.gui.monitor import Dashboard, displayed_utilization
from test_libdcmi import FakeDcmiLibrary
from test_monitor import fake_frame, options


# 25.5.2 / CANN 9.1.0. PCI address anonymized; no workload identities recorded.
SMI_310P3 = """
+--------------------------------------------------------------------------------------------------------+
| npu-smi 25.5.2                                   Version: 25.5.2                                       |
+-------------------------------+-----------------+------------------------------------------------------+
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
+===============================+=================+======================================================+
| 32896   310P3                 | OK              | NA           54                0     / 0             |
| 0       0                     | 0000:00:00.0    | 0            1904 / 44213                            |
+===============================+=================+======================================================+
+-------------------------------+-----------------+------------------------------------------------------+
| NPU     Chip                  | Process id      | Process name             | Process memory(MB)        |
+===============================+=================+======================================================+
| No running processes found in NPU 32896                                                                |
+===============================+=================+======================================================+
"""

MAPPING_310P3 = """
NPU ID       Chip ID       Chip Logic ID       Chip Name
32896        0             0                   Ascend 310P3
32896        1             -                   Mcu
"""


class Dcmi310P3(FakeDcmiLibrary):
    """Response contract observed on the visible 25.5.2 310P3 device."""

    def dcmi_get_card_num_list(self, count, cards, _list_len):
        count._obj.value = 1
        cards[0] = 32896
        return 0

    def dcmi_get_device_num_in_card(self, card_id, count):
        assert card_id == 32896
        count._obj.value = 1
        return 0

    def dcmi_get_device_chip_info_v2(self, card, chip, info):
        assert (card, chip) == (32896, 0)
        info._obj.npu_name = (ctypes.c_ubyte * 32)(*b'310P3')
        return 0

    def dcmi_get_device_board_id(self, card, chip, value):
        value._obj.value = 104
        return 0

    def dcmi_get_device_logic_id(self, value, card, chip):
        assert (card, chip) == (32896, 0)
        value._obj.value = 0
        return 0

    def dcmi_get_device_phyid_from_logicid(self, logical, value):
        assert logical == 0
        value._obj.value = 12
        return 0

    def dcmi_get_device_hbm_info(self, card, chip, info):
        self._record('unsupported_hbm')
        return -8255

    def dcmi_get_device_memory_info_v3(self, card, chip, info):
        info._obj.memory_size = 44213
        info._obj.memory_available = 44213 - 1904
        return 0

    def dcmi_get_device_power_info(self, card, chip, value):
        return -8255

    def dcmi_get_device_info(self, card, chip, main, sub, info, size):
        return -8255

    def dcmi_get_device_utilization_rate(self, card, chip, selector, value):
        if selector == 2:
            value._obj.value = 0
            return 0
        return -8255

    def dcmi_get_device_frequency(self, card, chip, selector, value):
        if selector == 7:
            value._obj.value = 960
            return 0
        return -8255

    def dcmi_get_device_resource_info(self, card, chip, entries, count):
        count._obj.value = 0
        return 0


def test_310p_dcmi_uses_ddr_fallback_without_fabricating_optional_metrics():
    library = Dcmi310P3()
    adapter = monitor.DcmiAdapter(library)
    for _ in range(2):
        frame = adapter.sample()
        assert monitor.healthy(frame) and frame['process_status'] == 'ok'
        assert len(frame['devices']) == 1 and frame['processes'] == []
        row = frame['devices'][0]
        assert (row['id'], row['key'], row['logical_id'], row['chip_physical_id']) == (
            0,
            '32896:0',
            0,
            12,
        )
        m = row['metrics']
        assert m['memory_used']['value'] == 1904 * 1024**2
        assert m['memory_total']['value'] == 44213 * 1024**2
        assert m['aicore']['value'] == 0 and m['aicore_mhz']['value'] == 960
        for name in ('npu_overall', 'power'):
            assert m[name]['value'] is None and m[name]['state'] == 'unsupported'
        assert row['power_reference']['value_w'] == 72
        assert row['power_reference']['query']['code'] == -8255
        assert row['power_scope'] != 'card-shared'
    assert library.call_counts['unsupported_hbm'] == 1  # Unsupported-query backoff.


def smi_frame(monkeypatch):
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    monkeypatch.setattr(
        monitor.subprocess,
        'run',
        lambda command, **kwargs: SimpleNamespace(
            stdout=MAPPING_310P3 if command[-1] == '-m' else SMI_310P3
        ),
    )
    frame = fake_frame('smi')
    frame.update(monitor.SmiAdapter().sample('@mapping'))
    return frame


def test_310p_ddr_missing_power_and_large_card_mapping(monkeypatch):
    frame = smi_frame(monkeypatch)
    assert monitor.healthy(frame) and frame['process_status'] == 'ok'
    assert len(frame['devices']) == 1 and frame['processes'] == []
    row = frame['devices'][0]
    assert (row['id'], row['card'], row['chip'], row['logical_id']) == (0, 32896, 0, 0)
    assert row['chip_physical_id'] is None
    assert row['metrics']['memory_total']['value'] == 44213 * 1024**2
    assert row['metrics']['memory_used']['value'] == 1904 * 1024**2
    assert row['metrics']['aicore']['value'] == 0
    assert row['metrics']['power']['value'] is None
    assert row['power_reference']['value_w'] == 72
    assert row['power_reference']['source'] == 'model-estimate'
    assert row['power_scope'] != 'card-shared'  # A3 inference must not leak into 310P.
    assert 'npu_overall' not in row['metrics']
    assert Dashboard(options(visible_ids=[0])).devices(frame) == [row]
    assert Dashboard(options(visible_ids=[32896])).devices(frame) == []
    assert Dashboard(options(only=[0])).devices(frame) == [row]
    assert Dashboard(options(only=[32896])).devices(frame) == []


@pytest.mark.parametrize('width', [80, 120, 180])
@pytest.mark.parametrize('mode', ['compact', 'full'])
@pytest.mark.parametrize('ascii_only', [False, True])
def test_310p_large_card_id_fits_native_layout(monkeypatch, width, mode, ascii_only):
    screen = Dashboard(options(monitor=mode, ascii=ascii_only))
    lines = screen.lines(smi_frame(monkeypatch), width, 60)
    rows = [line for line in lines if line.target == ('devices', 0)]
    text = '\n'.join(line.text for line in rows)
    assert '32896:0' in text and '0.00W' not in text and '0.0W' not in text
    assert all(len(WideString(line.text)) <= width - 1 for line in lines)
    if mode == 'full':
        assert 'NA / 72W' in text and '310P3' in text
    if width >= (100 if mode == 'full' else 124):
        assert 'UTL*' in text and '0%' in text.split('UTL*', 1)[1]
    assert 'NA' in text
    assert any('UTL*: AICore fallback (310P)' in line.text for line in lines)


@pytest.mark.parametrize(
    'raw,code,expected',
    [(169, 0, 16.9), (0, 0, 0), (-1, 0, None), (0, -8255, None), (0, -8006, None)],
)
def test_310p_optional_mcu_power_units_scope_and_failures(raw, code, expected):
    library = Dcmi310P3()

    def mcu(card, pointer):
        assert card == 32896
        pointer._obj.value = raw
        return code

    library.dcmi_mcu_get_power_info = mcu
    row = monitor.DcmiAdapter(library).sample()['devices'][0]
    assert row['metrics']['power']['value'] == expected
    assert row['power_source'] == 'dcmi_mcu_get_power_info'
    assert (row['power_scope'] == 'card-shared') == (expected is not None)
    assert row['power_reference']['value_w'] == 72  # Never substitute a rated reference.


def test_mcu_is_not_used_for_transient_errors_or_other_models():
    for model, code in [('Ascend910B3', -8255), ('310P3', -8006)]:
        library = Dcmi310P3()
        library.dcmi_mcu_get_power_info = lambda *args: pytest.fail('unexpected MCU query')
        library.dcmi_get_device_power_info = lambda *args: code
        adapter = monitor.DcmiAdapter(library)
        adapter.inventory[0]['model'] = model
        row = adapter.sample()['devices'][0]
        assert row['metrics']['power']['value'] is None
        assert row['power_source'] == 'dcmi_get_device_power_info'


def test_mcu_last_good_value_cannot_be_relabelled_as_chip_power():
    library = Dcmi310P3()

    def mcu(card, pointer):
        pointer._obj.value = 169
        return 0

    library.dcmi_mcu_get_power_info = mcu
    adapter = monitor.DcmiAdapter(library)
    assert adapter.sample()['devices'][0]['metrics']['power']['value'] == 16.9
    # Simulate a later re-probe changing from unsupported to a transient error.
    adapter.dcmi._negative.clear()
    adapter.dcmi._functions['dcmi_get_device_power_info'] = lambda *args: -8006
    row = adapter.sample()['devices'][0]
    assert row['metrics']['power']['value'] is None
    assert row['power_source'] == 'dcmi_get_device_power_info'


@pytest.mark.parametrize('state', ['unsupported', 'ok', 'stale', 'permission', 'error'])
def test_310p_display_fallback_preserves_raw_overall_and_history(state):
    frame = fake_frame()
    row = frame['devices'][0]
    row['model'] = 'Ascend310P3'
    row['metrics']['aicore'] = dict(value=42, state='ok')
    raw = row['metrics']['npu_overall'] = dict(
        value=71 if state in ('ok', 'stale') else None,
        state=state,
    )
    metric, source = displayed_utilization(row)
    if state == 'unsupported':
        assert metric['value'] == 42 and source == 'AICore fallback'
    else:
        assert metric is raw and source == 'NPU overall'
    screen = Dashboard(options())
    screen.lines(frame, 180, 60)
    assert screen.history['@util'][-1] == (
        42 if state == 'unsupported' else 71 if state == 'ok' else None
    )
    assert row['metrics']['npu_overall'] is raw
    row['model'] = 'Ascend910'
    assert displayed_utilization(row) == (raw, 'NPU overall')
