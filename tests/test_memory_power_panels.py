"""Native bandwidth sources and nvitop-like panels with card-scoped power."""

import copy
import curses
from unittest.mock import Mock

import pytest

from nputop.api import monitor
from nputop.gui.monitor import Dashboard, displayed_power, power_bar, draw_line
from nputop.gui.library.widestring import WideString
from test_libdcmi import FakeDcmiLibrary
from test_310p_compatibility import Dcmi310P3
from test_monitor import fake_frame, options
from test_power_reference import PowerLibrary


def test_hbm_query_is_shared_and_details_follow_current_snapshot():
    library = FakeDcmiLibrary()
    adapter = monitor.DcmiAdapter(library)
    for sample in range(2):
        rows = adapter.sample('*')['devices']
        assert library.call_counts['hbm'] == 3 * (sample + 1)
        assert 'utilization:10' not in library.call_counts
        for row in rows:
            m = row['metrics']
            assert m['memory_bandwidth']['value'] == 7
            assert m['memory_mhz']['value'] == 1600
            assert m['hbm_temperature']['value'] == 42
            assert m['memory_used']['value'] == (1024 + row['chip']) * 1024**2
            for name in ('memory_bandwidth', 'memory_mhz', 'hbm_temperature'):
                assert row['details'][name] == m[name]
                assert m[name]['source'] == 'dcmi_get_device_hbm_info'
    original = library.dcmi_get_device_hbm_info

    def changed(card, chip, info):
        original(card, chip, info)
        info._obj.bandwith_util_rate = 48
        info._obj.freq = 1800
        return 0

    library.dcmi_get_device_hbm_info = changed
    # Function bindings are fixed at initialization, as they are on real drivers.
    adapter.dcmi._functions['dcmi_get_device_hbm_info'] = changed
    row = adapter.sample('*')['devices'][0]
    assert row['details']['memory_bandwidth']['value'] == 48
    assert row['details']['memory_mhz']['value'] == 1800


class Bandwidth310P(Dcmi310P3):
    code = 0

    def dcmi_get_device_utilization_rate(self, card, chip, selector, value):
        self._record('util:' + str(selector))
        if selector == 5:
            value._obj.value = 51
            return self.code
        return super().dcmi_get_device_utilization_rate(card, chip, selector, value)

    def dcmi_get_device_frequency(self, card, chip, selector, value):
        if selector == 1:
            value._obj.value = 451
            return self.code
        return super().dcmi_get_device_frequency(card, chip, selector, value)


def test_310p_ddr_bandwidth_clock_and_stale_source():
    library = Bandwidth310P()
    adapter = monitor.DcmiAdapter(library)
    for code, state in ((0, 'ok'), (-8006, 'stale'), (-8002, 'permission')):
        library.code = code
        row = adapter.sample('*')['devices'][0]
        for name, value, selector in (('memory_bandwidth', 51, 5), ('memory_mhz', 451, 1)):
            m = row['metrics'][name]
            assert m['value'] == (None if state == 'permission' else value)
            assert m['state'] == state and m['source'].endswith('(%s)' % selector)
            assert row['details'][name] == m
        assert row['key'] == '32896:0'
    assert library.call_counts['unsupported_hbm'] == 1
    assert library.call_counts['util:10'] == 1


@pytest.mark.parametrize('code', [-8002, -8006, -1])
def test_failed_hbm_telemetry_does_not_switch_to_an_unrelated_counter(code):
    library = FakeDcmiLibrary()
    library.dcmi_get_device_hbm_info = lambda *args: code
    adapter = monitor.DcmiAdapter(library)
    metrics = adapter.sample()['devices'][0]['metrics']
    assert metrics['memory_bandwidth']['value'] is None
    assert metrics['memory_bandwidth']['state'] == monitor.ERROR_STATES.get(code, 'error')
    assert 'utilization:5' not in library.call_counts


def test_missing_optional_apis_and_invalid_percent_or_frequency_remain_unknown():
    library = FakeDcmiLibrary()
    original = library.dcmi_get_device_hbm_info

    def invalid(card, chip, info):
        original(card, chip, info)
        info._obj.bandwith_util_rate = 0xFFFFFFFF
        info._obj.freq = 0xFFFFFFFF
        return 0

    library.dcmi_get_device_hbm_info = invalid
    library.dcmi_get_device_utilization_rate = None
    library.dcmi_get_device_frequency = None
    row = monitor.DcmiAdapter(library).sample()['devices'][0]
    assert row['metrics']['memory_total']['value'] == 64 * 1024**3
    for name in ('memory_bandwidth', 'memory_mhz'):
        assert row['metrics'][name]['value'] is None
        assert row['metrics'][name]['state'] == 'unsupported'


def test_hbm_zero_in_known_vm_is_not_presented_as_measured_idle(monkeypatch):
    library = FakeDcmiLibrary()
    original = library.dcmi_get_device_hbm_info

    def zero(card, chip, info):
        original(card, chip, info)
        info._obj.bandwith_util_rate = 0
        return 0

    library.dcmi_get_device_hbm_info = zero
    for vm in (False, True):
        monkeypatch.setattr(monitor, 'known_virtual_machine', lambda: vm)
        adapter = monitor.DcmiAdapter(library)
        m = adapter.sample()['devices'][0]['metrics']['memory_bandwidth']
        assert m['value'] == (None if vm else 0)
        assert m['state'] == ('unavailable' if vm else 'ok')
        if vm:
            assert m['raw_value'] == 0 and 'passthrough VM' in m['reason']


@pytest.mark.parametrize('width', [80, 99, 100, 120, 123, 124, 139, 140, 180])
@pytest.mark.parametrize('ascii_only', [False, True])
def test_panel_breakpoints_keep_three_information_blocks_and_shared_power(width, ascii_only):
    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(PowerLibrary()).sample())
    original = copy.deepcopy(frame)
    edge = '|' if ascii_only else '│'
    for mode in ('full', 'compact'):
        screen = Dashboard(options(monitor=mode, ascii=ascii_only))
        lines = screen.lines(frame, width, 80)
        rows = [l for l in lines if l.target and l.target[0] == 'devices']
        assert {l.target[1] for l in rows} == {0, 1, 2}
        assert all(len(WideString(l.text)) <= width - 1 for l in lines)
        titles = [l for l in lines if l.style == 'section']
        assert titles[0].text.count(edge) == 4
        first = [l for l in rows if l.target == ('devices', 0)]
        sibling = [l for l in rows if l.target == ('devices', 1)]
        assert '123W / 949W' in first[-1].text.split(edge)[1]
        assert 'Card 5' in sibling[-1].text.split(edge)[1]
        assert '949W' not in sibling[-1].text
        assert ('MBW ' in first[0].text) == (mode == 'full' and width >= 140)
        assert ('PWR ' in first[-1].text) == (mode == 'full' and width >= 140)
        assert ('MEM ' in first[0].text) == (width >= 100)
        assert ('UTL ' in first[-1].text) == (width >= (100 if mode == 'full' else 124))
    assert frame == original


@pytest.mark.parametrize(
    'value,state,reference',
    [
        (170.2, 'ok', 949.2),
        (0, 'ok', 949.2),
        (125.6, 'stale', 949.2),
        (2000, 'ok', 949.2),
        (None, 'unsupported', 949.2),
        (120, 'ok', None),
        (120, 'ok', 0),
    ],
)
def test_power_bar_uses_unrounded_reference_and_preserves_missing_zero_stale(
    value, state, reference
):
    group = dict(metric=dict(value=value, state=state), reference_value=reference)
    for width in (29, 49):
        text, ratio = power_bar(group, width)
        expected = 100 * value / reference if value is not None and reference else None
        assert ratio == expected
        assert len(WideString(text)) <= width and text.endswith('ref')
        if expected is None:
            assert '█' not in text and 'NA ref' in text
        elif state == 'stale':
            assert '*' in text
        elif expected == 0:
            assert '0.0% ref' in text
        elif expected > 100:
            assert '210.7% ref' in text


def test_power_bar_highlights_for_either_die_without_brightening_unrelated_mbw():
    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(PowerLibrary()).sample())
    frame['processes'] = [
        dict(
            device='5:1',
            pid=123,
            created=4,
            user='test',
            memory=0,
            cpu=0,
            host_memory=0,
            command='test',
        )
    ]
    screen = Dashboard(options(monitor='full', ascii=False))
    screen.selection_active = True
    rows = [l for l in screen.lines(frame, 180, 80) if l.target == ('devices', 0)]
    assert rows[0].emphasis == rows[1].emphasis == 'muted'
    pwr_span = rows[1].spans[-1]
    assert pwr_span[3] == 'linked'
    window = Mock()
    draw_line(window, 0, rows[1], 180, dict(muted=curses.A_DIM, linked=curses.A_BOLD, border=0))
    attr = next(c[0][-1] for c in window.addstr.call_args_list if c[0][1] == pwr_span[0])
    assert attr & curses.A_BOLD and not attr & (curses.A_DIM | curses.A_REVERSE)
