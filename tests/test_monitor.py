import ctypes
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


from nputop.api import monitor
from nputop.gui.monitor import Dashboard
from test_libdcmi import FakeDcmiLibrary
from test_libascend import TEST_CASES


def options(**kwargs):
    return SimpleNamespace(
        **dict(
            dict(monitor='auto', interval=1, only=None, user=None, pid=None, ascii=True), **kwargs
        )
    )


def fake_frame(backend='dcmi', valid=True):
    metric = lambda value: {
        'value': value,
        'state': 'ok' if valid else 'unsupported',
        'at': time.monotonic(),
        'reason': None,
    }
    return {
        'devices': [
            {
                'id': 0,
                'key': '5:0',
                'card': 5,
                'chip': 0,
                'model': 'Fake910',
                'bus': '0000:00:00.0',
                'metrics': {
                    'memory_used': metric(1024),
                    'memory_total': metric(4096),
                    'aicore': metric(20),
                    'temperature': metric(40),
                    'power': metric(120),
                },
                'details': {},
                'process_state': 'ok',
            }
        ],
        'processes': [],
        'process_status': 'ok',
        'errors': [],
        'backend': backend,
        'requested_backend': 'auto',
        'driver': 'test',
        'cann': 'test',
        'reason': None,
        'sampled_at': time.monotonic(),
        'collection_ms': 1,
        'host': {
            'cpu': 2,
            'memory_used': 2048,
            'memory_total': 8192,
            'memory_percent': 25,
            'swap_percent': 0,
        },
        'capabilities': [],
    }


class Adapter:
    driver = 'test'

    def __init__(self, name, valid=True):
        self.name, self.valid = name, valid

    def sample(self, detail=None):
        return fake_frame(self.name, self.valid)

    def diagnostics(self):
        return []


def test_auto_fallback_requires_core_capabilities():
    smi = Mock(return_value=Adapter('smi'))
    manager = monitor.BackendManager('auto', lambda: Adapter('dcmi', False), smi)
    frame = manager.collect()
    assert frame['backend'] == 'smi'
    assert 'incomplete' in frame['reason']
    assert smi.call_count == 1
    manager.collect()
    assert smi.call_count == 1  # stable backend, no per-frame reprobe


def test_forced_backend_never_silently_falls_back():
    smi = Mock(side_effect=AssertionError('must not use SMI'))
    manager = monitor.BackendManager('dcmi', lambda: Adapter('dcmi', False), smi)
    assert manager.collect()['backend'] == 'dcmi'
    smi.assert_not_called()


def test_auto_initialization_failure_falls_back():
    def fail():
        raise OSError('missing library')

    frame = monitor.BackendManager('auto', fail, lambda: Adapter('smi')).collect()
    assert frame['backend'] == 'smi'
    assert 'missing library' in frame['reason']


def test_zero_dcmi_devices_tries_smi():
    dcmi = Adapter('dcmi')
    dcmi.sample = lambda detail: dict(fake_frame(), devices=[])
    assert (
        monitor.BackendManager('auto', lambda: dcmi, lambda: Adapter('smi')).collect()['backend']
        == 'smi'
    )


def test_transient_failure_preserves_value_then_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(monitor.time, 'monotonic', lambda: clock[0])
    store = monitor.MetricStore()
    assert store.metric('temp', 41)['state'] == 'ok'
    clock[0] += 1
    value = store.metric('temp', None, 'error', 'failed')
    assert (value['value'], value['state'], value['at']) == (41, 'stale', 100)
    clock[0] += 10
    assert store.metric('temp', None, 'error')['value'] is None
    assert store.metric('temp', None, 'permission')['state'] == 'permission'


@pytest.mark.parametrize('origin', [0.0, 5.0, 1000000.0])
@pytest.mark.parametrize('raises', [False, True])
def test_runtime_fallback_after_three_failures(monkeypatch, origin, raises):
    monkeypatch.setattr(monitor.time, 'monotonic', lambda: origin)
    dcmi = Adapter('dcmi')
    manager = monitor.BackendManager('auto', lambda: dcmi, lambda: Adapter('smi'))
    assert manager.collect()['backend'] == 'dcmi'
    dcmi.valid = False
    if raises:
        dcmi.sample = Mock(side_effect=OSError('temporary device failure'))
        for _ in range(2):
            with pytest.raises(OSError, match='temporary device failure'):
                manager.collect()
    else:
        assert manager.collect()['backend'] == 'dcmi'
        assert manager.collect()['backend'] == 'dcmi'
    assert manager.collect()['backend'] == 'smi'


def test_failed_fallback_at_clock_zero_is_still_rate_limited(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(monitor.time, 'monotonic', lambda: clock[0])
    dcmi = Adapter('dcmi')
    smi = Mock(side_effect=OSError('SMI not available'))
    manager = monitor.BackendManager('auto', lambda: dcmi, smi)
    manager.collect()
    dcmi.valid = False
    for _ in range(3):
        manager.collect()
    assert smi.call_count == 1
    clock[0] = 30.0
    manager.collect()
    assert smi.call_count == 1
    clock[0] = 61.0
    manager.collect()
    assert smi.call_count == 2


def test_missing_optional_symbol_does_not_fail_core():
    library = FakeDcmiLibrary()
    library.dcmi_get_pcie_link_bandwidth_info = None
    adapter = monitor.DcmiAdapter(library)
    frame = adapter.sample('5:0')
    assert monitor.healthy(frame)
    assert frame['devices'][0]['details']['pcie_tx']['state'] == 'unsupported'
    assert frame['devices'][0]['metrics']['aicore']['value'] == 7
    assert frame['devices'][0]['details']['npu_overall']['value'] == 63


def test_query_failure_not_reported_as_no_processes():
    library = FakeDcmiLibrary()
    library.dcmi_get_device_resource_info = lambda *args: -8002
    frame = monitor.DcmiAdapter(library).sample()
    assert frame['process_status'] == 'unavailable'
    assert frame['devices'][0]['process_state'] == 'permission'
    assert frame['processes'] == []
    assert frame['errors']


def test_unsupported_queries_are_backed_off():
    library = FakeDcmiLibrary()
    call = Mock(return_value=-8255)
    library.dcmi_get_device_utilization_rate = call
    backend = monitor.ObservedDcmi(library)
    assert backend._utilization(0, 13) is None
    assert backend._utilization(0, 13) is None
    assert call.call_count == 1
    # Capability keys include selector and device.
    backend._utilization(0, 2)
    backend._utilization(1, 13)
    assert call.call_count == 3


def test_910c_numeric_product_code_does_not_replace_model():
    library = FakeDcmiLibrary()

    def chip(_card, _chip, info):
        info._obj.chip_name = (ctypes.c_ubyte * 32)(*b'Ascend910\0')
        info._obj.npu_name = (ctypes.c_ubyte * 32)(*b'9382\0')
        return 0

    library.dcmi_get_device_chip_info_v2 = chip
    row = monitor.DcmiAdapter(library).sample()['devices'][0]
    assert row['model'] == 'Ascend910'
    assert row['product_code'] == '9382'


def test_smi_one_full_query_per_frame_and_correct_topology(monkeypatch):
    query = Mock(return_value=SimpleNamespace(stdout=TEST_CASES[2][0]))
    monkeypatch.setattr(monitor.subprocess, 'run', query)
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    frame = monitor.SmiAdapter().sample()
    query.assert_called_once()
    assert [row['key'] for row in frame['devices']] == ['0:0', '0:1', '1:0', '1:1']
    assert frame['devices'][1]['metrics']['power']['value'] is None
    assert frame['processes'][0]['device'] == '1:1'


def test_smi_missing_process_table_not_empty_success(monkeypatch):
    raw = TEST_CASES[0][0].split('| NPU     Chip')[0]
    monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    assert monitor.SmiAdapter().sample()['process_status'] == 'unavailable'


def test_diagnostics_redact_process_and_host_details():
    frame = fake_frame()
    frame['processes'] = [{'pid': 998877, 'user': 'secret-user', 'command': 'private-command'}]
    report = monitor.diagnostic_report(frame)
    text = str(report)
    assert not any(
        secret in text for secret in ('998877', 'secret-user', 'private-command', '0000:00:00.0')
    )
    assert report['devices'][0]['metrics']['aicore']['state'] == 'ok'


def test_dashboard_states_and_no_fabricated_values():
    frame = fake_frame()
    frame['process_status'] = 'unavailable'
    frame['errors'] = ['permission denied']
    frame['devices'][0]['metrics']['power']['value'] = None
    screen = Dashboard(options())
    text = '\n'.join(line.text for line in screen.lines(frame, 160, 40))
    assert 'No running NPU processes' not in text
    assert 'Process information unavailable' in text
    assert 'Compute M.' not in text and 'Fan' not in text
    assert 'AICore' in text and 'N/A' not in text


def test_history_resets_on_backend_switch():
    screen = Dashboard(options())
    screen.accept(fake_frame())
    assert len(screen.history['5:0']) == 1
    screen.accept(fake_frame('smi'))
    assert len(screen.history['5:0']) == 1


def test_stale_process_cannot_be_signaled(monkeypatch):
    frame = fake_frame()
    frame['sampled_at'] -= 20
    frame['processes'] = [{'device': '5:0', 'pid': 123, 'created': 1, 'memory': 10}]
    screen = Dashboard(options())
    screen.confirm = frame['processes'][0]
    process = Mock()
    monkeypatch.setattr(monitor.psutil, 'Process', process)
    screen.handle(ord('y'), frame, SimpleNamespace(error=None))
    process.assert_not_called()
    assert 'stale' in screen.notice


# Spawned test workers exercise real process shutdown without any hardware calls.
def hung_worker(connection, mode):
    if mode != 'smi':
        time.sleep(60)
    else:
        while True:
            connection.recv()
            connection.send(('frame', fake_frame('smi')))


def test_hung_dcmi_worker_is_stopped_before_smi_fallback(monkeypatch):
    monkeypatch.setattr(monitor, '_worker', hung_worker)
    sampler = monitor.Sampler(timeout=1)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and sampler.frame is None:
            sampler.poll()
            time.sleep(0.02)
        assert sampler.frame['backend'] == 'smi'
        assert 'timed out' in sampler.frame['reason']
    finally:
        sampler.close()
    assert sampler.process is None


def test_forced_dcmi_timeout_does_not_use_smi(monkeypatch):
    monkeypatch.setattr(monitor, '_worker', hung_worker)
    sampler = monitor.Sampler(mode='dcmi', timeout=0.5)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and sampler.error is None:
            sampler.poll()
            time.sleep(0.02)
        assert 'timed out' in sampler.error
        assert sampler.worker_mode == 'dcmi'
        assert sampler.frame is None
    finally:
        sampler.close()


def test_a5_overall_utilization_is_not_mislabeled_aicore(monkeypatch):
    from test_libascend_a5 import A5_OUTPUT

    monkeypatch.setattr(
        monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=A5_OUTPUT)
    )
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 10)
    frame = monitor.SmiAdapter().sample()
    row = frame['devices'][0]
    assert row['metrics']['aicore']['value'] is None
    assert row['metrics']['npu_overall']['value'] == 20
    assert monitor.healthy(frame)
    assert row['primary_utilization'] == 'npu_overall'


def test_malformed_smi_process_rows_are_not_reported_as_empty(monkeypatch):
    raw = TEST_CASES[0][0].replace('124528', 'unknown')
    monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    frame = monitor.SmiAdapter().sample()
    assert frame['process_status'] == 'unavailable'


def test_diagnostic_error_does_not_leak_library_paths():
    report = monitor.diagnostic_report(None, 'failed /home/private-user/lib/libdcmi.so')
    assert 'private-user' not in str(report)


def test_selected_details_only_query_one_chip():
    library = FakeDcmiLibrary()
    query = Mock(wraps=library.dcmi_get_pcie_link_bandwidth_info)
    library.dcmi_get_pcie_link_bandwidth_info = query
    adapter = monitor.DcmiAdapter(library)
    adapter.sample('5:1')
    assert query.call_count == 1
    adapter.sample('5:1')
    assert query.call_count == 1  # selected details are on the slow cadence


def test_narrow_dashboard_keeps_power_and_temperature_visible():
    lines = Dashboard(options()).lines(fake_frame(), 80, 24)
    row = next(line.text for line in lines if '5:0' in line.text and '120W' in line.text)
    assert row.index('120W') + len('120W') <= 79


def test_smi_missing_memory_is_unknown_not_zero(monkeypatch):
    raw = TEST_CASES[0][0].replace('20701/ 65536', '--').replace('20687/ 65536', '--')
    # Remove the unused DDR 0/0 fields as well.
    raw = raw.replace('0    / 0', '--')
    monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    frame = monitor.SmiAdapter().sample()
    assert frame['devices'][0]['metrics']['memory_total']['value'] is None
    assert not monitor.healthy(frame)


def test_classic_panels_fit_and_preserve_all_devices_on_tall_terminal():
    import copy

    frame = fake_frame()
    frame['devices'] = [copy.deepcopy(frame['devices'][0]) for _ in range(16)]
    for i, row in enumerate(frame['devices']):
        row.update(id=i, key=f'{i // 2}:{i % 2}', card=i // 2, chip=i % 2)
    screen = Dashboard(options(ascii=False))
    lines = screen.lines(frame, 160, 80)
    device_lines = [line for line in lines if line.target and 'UTL ' in line.text]
    assert len(device_lines) == 16
    assert sum('120W' in line.text for line in lines) == 8  # one power cell per card
    assert all(
        'UTL ' in line.text and '[' not in line.text and line.text.rstrip().endswith('│')
        for line in device_lines
    )
    assert all(len(line.text) <= 159 for line in device_lines)
    assert any('Host / NPU history' in line.text for line in lines)
    assert any('Processes' in line.text for line in lines)
    assert all(line.style == 'warning' for line in device_lines)  # moderate load, nvitop thresholds
    screen.lines(frame, 80, 24)
    assert len(screen.history['@cpu']) == 1  # redraw/resize is not another sample


def test_diagnostics_are_in_details_not_main_header():
    screen = Dashboard(options())
    frame = fake_frame()
    assert 'sample 1 ms' not in '\n'.join(x.text for x in screen.lines(frame, 160, 48))
    screen.expanded = True
    assert 'sample 1 ms' in '\n'.join(x.text for x in screen.lines(frame, 160, 48))


def test_device_dividers_continue_through_rows_and_double_header():
    screen = Dashboard(options(ascii=False))
    lines = screen.lines(fake_frame(), 160, 60)
    header = next(x.text for x in lines if 'Memory used / total' in x.text)
    row = next(x.text for x in lines if '120W' in x.text)
    columns = [i for i, char in enumerate(header) if char == '│']
    assert len(columns) == 4  # identity/sensors, memory and utilization blocks
    assert [i for i, char in enumerate(row) if char == '│'][:4] == columns
    header_rule = lines[lines.index(next(x for x in lines if x.text == header)) + 1].text
    assert '═' in header_rule
    assert all(header_rule[i] == '╪' for i in columns[1:-1])
    assert any('┼' in line.text for line in lines)
    assert any(line.text.startswith('╒') for line in lines)
    assert any(line.text.startswith('╘') for line in lines)


def test_distinct_aicore_and_overall_selectors_and_bars():
    library = FakeDcmiLibrary()

    def utilization(card, chip, selector, value):
        value._obj.value = {2: 20, 13: 80}.get(selector, 0)
        return 0

    library.dcmi_get_device_utilization_rate = utilization
    adapter = monitor.DcmiAdapter(library)
    frame = fake_frame()
    frame.update(adapter.sample())
    assert frame['devices'][0]['metrics']['aicore']['value'] == 20
    assert frame['devices'][0]['metrics']['npu_overall']['value'] == 80
    row = '\n'.join(
        x.text for x in Dashboard(options()).lines(frame, 160, 60) if x.target == ('devices', 0)
    )
    assert '20%' in row and '80%' in row
    assert '[' not in row and ']' not in row
    assert row.split('UTL ')[1].count('#') > 5


def test_optional_overall_is_not_replaced_or_required():
    library = FakeDcmiLibrary()
    original = library.dcmi_get_device_utilization_rate
    library.dcmi_get_device_utilization_rate = lambda c, d, s, v: (
        -8255 if s == 13 else original(c, d, s, v)
    )
    frame = fake_frame()
    frame.update(monitor.DcmiAdapter(library).sample())
    assert monitor.healthy(frame)
    assert frame['devices'][0]['metrics']['npu_overall']['value'] is None
    row = next(x.text for x in Dashboard(options()).lines(frame, 160, 60) if 'UTL ' in x.text)
    assert '--' in row.split('UTL ')[1]


def test_topology_comes_from_driver_for_single_dual_and_mixed_cards():
    for counts in ((1, 1), (2, 2), (1, 2)):
        library = FakeDcmiLibrary()

        def count(card, pointer):
            pointer._obj.value = counts[0 if card == 5 else 1]
            return 0

        library.dcmi_get_device_num_in_card = count
        adapter = monitor.DcmiAdapter(library)
        expected = [(card, chip) for card, n in zip((5, 8), counts) for chip in range(n)]
        frame = adapter.sample()
        assert [(r['card'], r['chip']) for r in frame['devices']] == expected
        assert {p['device'] for p in frame['processes']} <= {f'{c}:{d}' for c, d in expected}


def interaction_frame():
    import copy

    frame = fake_frame()
    second = copy.deepcopy(frame['devices'][0])
    second.update(id=1, key='8:0', card=8)
    frame['devices'].append(second)
    frame['processes'] = [
        dict(device='8:0', pid=99, memory=300, created=1, command='demo-b'),
        dict(device='5:0', pid=88, memory=100, created=2, command='demo-a'),
        dict(device='5:0', pid=77, memory=200, created=3, command='demo-c'),
    ]
    return frame


def test_mouse_process_selection_group_separators_and_linked_chip():
    import curses

    screen = Dashboard(options(ascii=False))
    frame = interaction_frame()
    lines = screen.lines(frame, 160, 60)
    targets = [(i, x) for i, x in enumerate(lines) if x.target and x.target[0] == 'processes']
    assert len(targets) == 3
    assert '77' in targets[0][1].text  # within-chip memory sort
    assert targets[1][0] - targets[0][0] == 1
    assert targets[2][0] - targets[1][0] == 2  # between-chip separator
    y = targets[2][0]
    screen.mouse(5, y, curses.BUTTON1_PRESSED, frame, Mock())
    lines = screen.lines(frame, 160, 60)
    assert screen.focus == 'processes'
    linked = [x for x in lines if x.emphasis == 'linked']
    assert linked and {line.target for line in linked} == {('devices', 1)}
    assert any('8:0' in line.text for line in linked)
    assert any(x.style == 'selected' and '99' in x.text for x in lines)
    assert __import__(
        'nputop.version', fromlist=['__preview_version__']
    ).__preview_version__ in '\n'.join(x.text for x in lines)
    screen.handle(curses.KEY_UP, frame, Mock())
    screen.lines(frame, 160, 60)
    assert screen.process_index == 1
    # Reordering updates keeps selection attached to the same process identity.
    frame['processes'][1]['memory'] = 999
    screen.lines(frame, 160, 60)
    assert screen.selected_process[1] == 88


def test_smi_topology_preserved_for_all_existing_model_fixtures(monkeypatch):
    for raw, expected in TEST_CASES:
        monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
        monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
        frame = monitor.SmiAdapter().sample()
        assert {(r['card'], r['chip']) for r in frame['devices']} == {
            (r['npu_id'], r['chip_id']) for r in expected.values()
        }


def test_terminate_confirmation_survives_idle_and_signals_only_test_child(monkeypatch):
    import subprocess
    import sys
    import curses
    import psutil

    monkeypatch.setattr('nputop.gui.actions.host_pid_namespace', lambda: 'test-host-namespace')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        frame = fake_frame()
        frame['processes'] = [
            dict(
                device='5:0',
                pid=child.pid,
                memory=0,
                created=psutil.Process(child.pid).create_time(),
                command='test-owned sleeper',
                signal_allowed=True,
                pid_namespace='test-host-namespace',
            )
        ]
        screen = Dashboard(options())
        screen.focus = 'processes'
        sampler = SimpleNamespace(error=None)
        screen.handle(curses.KEY_HOME, frame, sampler)
        screen.handle(ord('T'), frame, sampler)
        for _ in range(25):
            screen.handle(-1, frame, sampler)
        screen.handle(curses.KEY_RESIZE, frame, sampler)
        assert screen.confirm['pid'] == child.pid
        assert 'y confirm' in '\n'.join(x.text for x in screen.lines(frame, 160, 60))
        assert child.poll() is None
        screen.handle(ord('y'), frame, sampler)
        assert child.wait(timeout=3) == -15
        assert 'SIGTERM sent' in screen.notice
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


def test_clean_footer_and_explicit_terminate_cancellation():
    import curses

    screen = Dashboard(options())
    frame = interaction_frame()
    for row in frame['processes']:
        row.update(signal_allowed=True, pid_namespace='test-host-namespace')
    screen.focus = 'processes'
    screen.handle(curses.KEY_HOME, frame, Mock())
    screen.handle(ord('k'), frame, Mock())
    assert screen.confirm is not None
    screen.handle(27, frame, Mock())
    assert screen.confirm is None
    lines = screen.lines(frame, 160, 60)
    assert not any(x.style == 'footer' or 'q quit  |' in x.text for x in lines)


def test_moving_percentage_and_fixed_clock_fit():
    from nputop.gui.monitor import moving_bar

    rows = [
        moving_bar('UTL', {'value': v, 'state': 'ok'}, 50, False, '@ 1800MHz') for v in (0, 25, 100)
    ]
    assert rows[0].index('0%') < rows[1].index('25%') < rows[2].index('100%')
    assert all(len(row) == 50 and row.endswith('@ 1800MHz') for row in rows)
    assert len(moving_bar('UTL', {'value': 100, 'state': 'ok'}, 17, False, '@ 1800MHz')) == 17


def test_braille_history_is_mirrored_and_missing_samples_are_blank():
    from nputop.gui.monitor import dot_history

    top = dot_history([None, 100, 25], 3, 2)
    bottom = dot_history([None, 100, 25], 3, 2, mirrored=True)
    assert all(line[0] == ' ' for line in top + bottom)
    assert top[0][1] == top[1][1] == '⣿'
    assert top[0][2] == bottom[1][2] == ' '
    assert top[1][2] == '⣤' and bottom[0][2] == '⠛'


def test_current_frequency_is_collected_without_detail_queries():
    adapter = monitor.DcmiAdapter(FakeDcmiLibrary())
    frame = adapter.sample()
    assert frame['devices'][0]['metrics']['aicore_mhz']['value'] == 800
    assert frame['devices'][0]['details'] == {}
