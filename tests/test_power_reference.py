"""Power hypotheses must not become fictitious per-die enforced limits."""

import copy
import ctypes
from types import SimpleNamespace

import pytest

from nputop.api import libascend, libdcmi, monitor
from nputop.api.power import power_reference
from nputop.gui.monitor import Dashboard, displayed_power
from nputop.gui.library.widestring import WideString
from test_libascend import TEST_CASES
from test_libdcmi import FakeDcmiLibrary
from test_monitor import fake_frame, options


class PowerLibrary(FakeDcmiLibrary):
    raw = 949200
    code = 0
    size = 36
    board = 0xB1

    def dcmi_get_device_chip_info_v2(self, card, chip, info):
        info._obj.chip_name = (ctypes.c_ubyte * 32)(*b'Ascend910')
        info._obj.npu_name = (ctypes.c_ubyte * 32)(*b'9382')
        return 0

    def dcmi_get_device_board_id(self, card, chip, value):
        value._obj.value = self.board
        return 0

    def dcmi_get_device_info(self, card, chip, main, sub, info, size):
        self._record('rated')
        assert (main, sub) == (8, 10)
        assert size._obj.value == 36
        info._obj.soc_rated_power = self.raw
        size._obj.value = self.size
        return self.code


def test_a3_card_reference_is_cached_without_changing_per_chip_current_power():
    lib = PowerLibrary()
    adapter = monitor.DcmiAdapter(lib)
    for _ in range(2):
        frame = adapter.sample()
        assert [(r['card'], r['chip']) for r in frame['devices']] == [(5, 0), (5, 1), (8, 0)]
        for row in frame['devices']:
            ref = row['power_reference']
            assert ref['value_w'] == 949.2  # Raw card reference; display allocation is separate.
            assert ref['scope'] == 'card-shared'
            assert row['power_scope'] == 'driver-reported; scope unverified'
            assert ref['source'] == 'dcmi-rated-inferred' and ref['estimated']
            assert ref['query']['raw'] == 949200 and ref['query']['size'] == 36
            assert row['metrics']['power']['value'] == 123.4
            assert 'power_limit' not in row['metrics']
    assert lib.call_counts['rated'] == 3  # Static read, not per refresh.
    report = monitor.diagnostic_report(dict(fake_frame(), **frame))
    assert report['devices'][0]['power_reference']['query']['raw'] == 949200
    assert 'bus' not in report['devices'][0]


@pytest.mark.parametrize(
    'code,state',
    [
        (None, 'unsupported'),
        (-8002, 'permission'),
        (-8006, 'timeout'),
        (-8013, 'unsupported'),
        (-1, 'error'),
    ],
)
def test_rated_failure_is_optional_and_preserves_reason(code, state):
    lib = PowerLibrary()
    if code is None:
        lib.dcmi_get_device_info = None
    else:
        lib.code = code
    frame = monitor.DcmiAdapter(lib).sample()
    assert monitor.healthy(frame)
    for row in frame['devices']:
        ref = row['power_reference']
        assert ref['value_w'] is None and ref['query']['raw'] is None
        assert ref['query']['state'] == state
        assert row['power_scope'] == 'driver-reported; scope unverified'


@pytest.mark.parametrize(
    'raw,size', [(0, 36), (0xFFFFFFFF, 36), (949, 36), (9492000, 36), (949200, 4), (949200, 40)]
)
def test_invalid_units_values_or_abi_do_not_become_a_cap(raw, size):
    lib = PowerLibrary()
    lib.raw, lib.size = raw, size
    ref = monitor.DcmiAdapter(lib).sample()['devices'][0]['power_reference']
    assert ref['value_w'] is None
    if size != 36:
        assert ref['query']['state'] == 'invalid-size'


def test_single_die_910b_and_old_model_fallback_remain_distinct():
    data = dict(raw=364600, state='ok')
    ref = power_reference('Ascend910B3', libascend._POWER_LIMIT, telemetry=data)
    assert ref['value_w'] == 364.6 and ref['scope'] == 'unknown'
    ref = power_reference(
        '910B3', libascend._POWER_LIMIT, telemetry=dict(raw=None, state='permission')
    )
    assert ref['value_w'] == 350 and ref['source'] == 'model-estimate'
    assert ref['query']['state'] == 'permission'
    assert power_reference('Ascend910', libascend._POWER_LIMIT)['value_w'] is None
    assert power_reference('910C', libascend._POWER_LIMIT)['value_w'] is None
    assert power_reference('910B2C', libascend._POWER_LIMIT)['value_w'] is None


def test_two_chip_card_and_unknown_family_do_not_imply_a3():
    adapter = monitor.DcmiAdapter(FakeDcmiLibrary())
    for row in adapter.sample()['devices']:
        assert row['power_reference']['value_w'] is None
        assert row['power_scope'] != 'card-shared'
    ref = power_reference('310P3', libascend._POWER_LIMIT, telemetry=dict(raw=949200, state='ok'))
    assert ref['value_w'] == 72 and ref['scope'] == 'unknown'
    assert ref['source'] == 'model-estimate'


@pytest.mark.parametrize('raw,expected', TEST_CASES)
def test_smi_reference_uses_existing_model_table_without_extra_commands(monkeypatch, raw, expected):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=raw)

    monkeypatch.setattr(monitor.subprocess, 'run', run)
    monkeypatch.setattr(libascend, '_smi_timeout', lambda: 3)
    frame = monitor.SmiAdapter().sample()
    assert calls == [['npu-smi', 'info']]
    for row in frame['devices']:
        model = row['model']
        ref = row['power_reference']
        assert ref['value_w'] == (None if model == '910C' else libascend._POWER_LIMIT.get(model))
        if model == '910C':
            assert row['power_scope'] == 'driver-reported; scope unverified'


@pytest.mark.parametrize('width', [80, 120, 180])
@pytest.mark.parametrize('ascii_only', [False, True])
def test_full_power_cell_spans_dies_and_preserves_raw_details(width, ascii_only):
    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(PowerLibrary()).sample())
    frame['devices'][1]['metrics']['power']['value'] = 124.6
    screen = Dashboard(options(monitor='full', ascii=ascii_only))
    lines = screen.lines(frame, width, 60)
    device = [line for line in lines if line.target == ('devices', 0)]
    assert '5:0' in device[0].text and '2 dies' in device[0].text
    assert '125W / 949W' in device[1].text
    assert all('~949' not in line.text and '123.4-124.6' not in line.text for line in lines)
    assert all('474.6' not in line.text and 'Wc' not in line.text for line in lines)
    assert all(len(WideString(line.text)) <= width - 1 for line in lines)
    middle = lines[lines.index(device[1]) + 1].text
    assert ('-' if ascii_only else '─') * 33 in middle
    last = [line for line in lines if line.target == ('devices', 1)][-1]
    assert 'Card 5' in last.text and '949W' not in last.text
    boundary = lines[lines.index(last) + 1].text
    assert ('=' if ascii_only else '═') in boundary
    screen.expanded = True
    text = '\n'.join(line.text for line in screen.lines(frame, 180, 80))
    assert 'Card 5 · 2 dies' in text.replace(' - ', ' · ')
    assert 'not a sum or average' in text
    assert '5:0: 123.4W (ok)' in text and '5:1: 124.6W (ok)' in text
    assert 'Raw power: 123.4W (driver-reported; scope unverified)' in text
    assert 'not a verified enforced power limit' in text and 'mW inferred' in text
    assert 'source 5:1; newest valid reading' in text


@pytest.mark.parametrize('mode,height', [('full', 80), ('compact', 60), ('auto', 24)])
def test_power_group_survives_filter_resize_and_nonsequential_ids(mode, height):
    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(PowerLibrary()).sample())
    frame['devices'][0]['metrics']['power']['value'] = 170.0
    frame['devices'][1]['metrics']['power']['value'] = 170.2
    frame['devices'][0]['id'], frame['devices'][1]['id'] = 12, 39
    # Interleaving cards in enumeration must not combine adjacent display IDs.
    frame['devices'] = [frame['devices'][0], frame['devices'][2], frame['devices'][1]]
    before = copy.deepcopy(frame)
    for only in (None, [12], [39]):
        screen = Dashboard(options(monitor=mode, only=only))
        lines = screen.lines(frame, 180, height)
        text = '\n'.join(line.text for line in lines)
        assert (
            sum(
                '170W / 949W' in l.text or '170W/949W' in l.text
                for l in lines
                if l.target and l.target[0] == 'devices'
            )
            == 1
        )
        assert '340.2W' not in text and '85.1W' not in text
        if only is not None:
            assert ('1/2 dies' if screen.row_height == 3 else '2c 170W') in text
        else:
            assert [row['id'] for row in screen.devices(frame)] == [12, 39, 2]
    assert frame == before


@pytest.mark.parametrize(
    'state,value,expected',
    [('ok', 0, '0.0W'), ('stale', 125.6, '123.4W'), ('unsupported', None, '123.4W')],
)
def test_group_preserves_zero_staleness_and_missing_without_substitution(state, value, expected):
    rows = monitor.DcmiAdapter(PowerLibrary()).sample()['devices']
    rows[0]['metrics']['power'].update(state=state, value=value, at=20)
    rows[1]['metrics']['power']['at'] = 10
    group = displayed_power(rows[0], rows)
    assert group['current'] == expected
    assert group['reference'] == '949.2W' and group['label'] == '2 dies'
    rows[1]['metrics']['power'].update(state='permission', value=None)
    if value is None:
        assert displayed_power(rows[0], rows)['current'] == 'NA'
    elif state == 'stale':
        assert displayed_power(rows[0], rows)['current'] == '125.6W*'


def test_single_visible_a3_chip_keeps_explicit_whole_card_reference():
    rows = monitor.DcmiAdapter(PowerLibrary()).sample()['devices']
    group = displayed_power(rows[-1], rows)
    assert not group['grouped'] and group['current'] == '123.4W'
    assert group['reference'] == '949.2W'
    assert rows[-1]['power_reference']['value_w'] == 949.2


def test_310p_mcu_and_910b_values_are_not_divided():
    row = fake_frame()['devices'][0]
    for model, scope in [('310P3', 'card-shared'), ('910B3', 'driver-reported; scope unverified')]:
        row.update(
            model=model,
            power_scope=scope,
            power_reference=dict(value_w=72 if model == '310P3' else 364.6, scope='unknown'),
        )
        group = displayed_power(row, [row])
        assert not group['grouped'] and group['current'] == '120.0W'
        assert group['reference'] == ('72W' if model == '310P3' else '364.6W')
        # Unknown multi-chip designs can group raw readings, but cannot turn
        # per-chip references into an invented whole-card cap or die count.
        group = displayed_power(row, [row, dict(row, chip=7)])
        assert group['reference'] == 'NA' and group['label'] == '2 chips'


def test_group_handles_more_than_two_chips_and_missing_rated_reference():
    rows = monitor.DcmiAdapter(PowerLibrary()).sample()['devices'][:2]
    third = copy.deepcopy(rows[1])
    third.update(chip=7, key='5:7', id=40)
    third['metrics']['power']['value'] = 125.6
    third['metrics']['power']['at'] += 1
    rows.append(third)
    group = displayed_power(rows[0], rows)
    assert group['label'] == '3 dies' and group['current'] == '125.6W'
    for row in rows:
        row['power_reference']['value_w'] = None
    assert displayed_power(rows[0], rows)['reference'] == 'NA'


def test_power_source_is_latest_sample_not_largest_value_or_inventory_order():
    rows = monitor.DcmiAdapter(PowerLibrary()).sample()['devices'][:2]
    rows[0]['metrics']['power'].update(value=170.2, at=10)
    rows[1]['metrics']['power'].update(value=169.8, at=20)
    group = displayed_power(rows[0], list(reversed(rows)))
    assert group['current'] == '169.8W' and group['current_source'] == '5:1'
    rows[0]['metrics']['power']['at'] = 20
    group = displayed_power(rows[1], list(reversed(rows)))
    assert group['current'] == '170.2W' and group['current_source'] == '5:0'
    for row in rows:
        row['metrics']['power']['state'] = 'stale'
    rows[1]['metrics']['power']['at'] = 30
    assert displayed_power(rows[0], rows)['current'] == '169.8W*'


def test_conflicting_card_references_are_not_a_fabricated_limit_or_range():
    rows = monitor.DcmiAdapter(PowerLibrary()).sample()['devices'][:2]
    rows[1]['power_reference']['value_w'] = 950.0
    assert displayed_power(rows[0], rows)['reference'] == 'NA'
    rows[1]['power_reference']['value_w'] = None
    assert displayed_power(rows[0], rows)['reference'] == '949.2W'


def test_compact_filtered_power_keeps_slash_units_and_stale_marker():
    from nputop.gui.monitor import power_pair

    text = power_pair(dict(current='170.2W*', reference='949.2W'), 18, '2c ')
    assert len(text) <= 18 and '170.2W*' in text and '/949.2W' in text


def test_compact_layout_preserves_model_variant_and_power_column():
    frame = fake_frame()
    frame['devices'][0]['model'] = 'Ascend910B3'
    for width in (120, 180):
        lines = Dashboard(options(monitor='compact')).lines(frame, width, 60)
        row = next(line.text for line in lines if line.target == ('devices', 0))
        assert '910B3' in row and '120W' in row and 'MEM ' in row


@pytest.mark.parametrize('ascii_only', [True, False])
def test_shared_power_highlights_when_second_die_process_selected(ascii_only):
    import curses
    from unittest.mock import Mock
    from nputop.gui.monitor import draw_line

    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(PowerLibrary()).sample())
    screen = Dashboard(options(ascii=ascii_only, monitor='full'))
    screen.focus, screen.selection_active = 'processes', True
    frame['processes'] = [
        dict(
            device='5:1',
            pid=123,
            created=4,
            user='test',
            memory=1024,
            cpu=0,
            host_memory=0,
            command='test',
        )
    ]
    lines = screen.lines(frame, 180, 70)
    first = next(line for line in lines if line.target == ('devices', 0) and line.shared_power)
    assert first.emphasis == 'muted' and first.shared_power[2] == 'linked'
    window = Mock()
    draw_line(
        window, 0, first, 180, dict(normal=0, border=0, muted=curses.A_DIM, linked=curses.A_BOLD)
    )
    start, length, _ = first.shared_power
    call = next(
        c for c in window.addstr.call_args_list if c[0][1] == start and len(c[0][2]) == length
    )
    assert call[0][-1] & curses.A_BOLD and not call[0][-1] & curses.A_REVERSE


def test_legacy_public_api_stays_compatible(monkeypatch):
    libascend._update_cache(TEST_CASES[2][0].replace('Ascend910', '910C'))
    monkeypatch.setattr(libascend, '_update_cache', lambda: None)
    assert libascend.ascendDeviceGetPowerLimit(0) == 350
    backend = libdcmi.DcmiBackend(PowerLibrary())
    monkeypatch.setattr(libascend, '_DCMI_BACKEND', backend)
    assert backend.power_usage(0) == 123400  # Public API still milliwatts.
    assert libascend.ascendDeviceGetPowerLimit(0) == 'N/A'  # No inferred cap injected.
