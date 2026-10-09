"""Host inspection contracts and actual owned-worker lifecycle."""

import ctypes
import os
import time
from unittest.mock import Mock

import psutil
import pytest

from nputop.api import inspection, libdcmi
from nputop.api.visibility import parse_mapping, visible_ids
from test_libdcmi import FakeDcmiLibrary


def test_optional_identity_mapping_uses_driver_ids_not_inventory_position():
    library = FakeDcmiLibrary()

    def logical(pointer, card, chip):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = card * 10 + chip + 3
        return 0

    def physical(logic, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint))[0] = logic + 100
        return 0

    library.dcmi_get_device_logic_id = logical
    library.dcmi_get_device_phyid_from_logicid = physical
    backend = libdcmi.DcmiBackend(library)
    ref = backend._devices[0]
    ids = backend.device_ids(0)
    assert ids['logical_id'] == ref.card_id * 10 + ref.chip_id + 3
    assert ids['chip_physical_id'] == ids['logical_id'] + 100
    assert libdcmi.DcmiBackend(FakeDcmiLibrary()).device_ids(0)['logical_id'] is None


def test_mapping_requires_explicit_columns_and_unique_logical_ids():
    header = 'NPU ID  Chip ID  Chip Logic ID  Chip Phy-ID  Chip Name\n'
    rows = ' 4 0 2 9 Ascend310P\n 7 0 5 12 Ascend910\n 7 1 6 13 Ascend910\n 7 2 - - Mcu\n'
    mapped = parse_mapping(header + rows)
    assert mapped['4:0']['logical_id'] == 2
    assert mapped['7:1']['chip_physical_id'] == 13
    assert len(mapped) == 3
    assert not parse_mapping(rows)
    assert not parse_mapping(header + rows + ' 8 0 2 20 Ascend310\n')
    assert visible_ids({}) is None
    assert visible_ids({'ASCEND_RT_VISIBLE_DEVICES': ''}) == []
    assert visible_ids({'ASCEND_RT_VISIBLE_DEVICES': '2,5', 'CUDA_VISIBLE_DEVICES': '0'}) == [2, 5]
    for raw in ('-1', '2, 3', '2,2', '3,1', 'a'):
        with pytest.raises(ValueError):
            visible_ids({'ASCEND_RT_VISIBLE_DEVICES': raw})


def test_910b_mapping_without_physical_column_preserves_logical_id():
    # Observed on 910B3 / driver 25.2.3: the MCU has no runtime logical ID.
    header = 'NPU ID   Chip ID   Chip Logic ID   Chip Name\n'
    rows = '3 0 0 Ascend 910B3\n3 1 - Mcu\n'
    assert parse_mapping(header + rows) == {
        '3:0': dict(logical_id=0, chip_physical_id=None, id_source='npu-smi -m'),
    }
    assert not parse_mapping(rows)
    assert not parse_mapping(header + rows + '4 0 0 Ascend 910B3\n')
    assert not parse_mapping(header + rows + '3 0 2 Ascend 910B3\n')
    assert not parse_mapping('NPU ID Chip Logic ID Chip ID Chip Name\n' + rows)
    assert (
        parse_mapping('NPU ID Chip ID Chip Logic ID Chip Phy-ID Chip Name\n3 0 0 - Ascend 910B3\n')[
            '3:0'
        ]['chip_physical_id']
        is None
    )


def test_910b_smi_visible_filter_uses_logical_id_not_card_number(monkeypatch):
    from types import SimpleNamespace
    from nputop.api.monitor import SmiAdapter
    from nputop.gui.monitor import Dashboard
    from test_libascend import TEST_CASES
    from test_monitor import fake_frame, options

    mapping = 'NPU ID Chip ID Chip Logic ID Chip Name\n3 0 0 Ascend 910B3\n3 1 - Mcu\n'
    monkeypatch.setattr('nputop.api.monitor.libascend._smi_timeout', lambda: 3)
    monkeypatch.setattr(
        'nputop.api.monitor.subprocess.run',
        lambda cmd, **kwargs: SimpleNamespace(
            stdout=mapping if cmd[-1] == '-m' else TEST_CASES[-1][0],
        ),
    )
    value = fake_frame('smi')
    value.update(SmiAdapter().sample('@mapping'))
    assert [d['key'] for d in Dashboard(options(visible_ids=[0])).devices(value)] == ['3:0']
    assert Dashboard(options(visible_ids=[3])).devices(value) == []
    assert [d['key'] for d in Dashboard(options(only=[0])).devices(value)] == ['3:0']


def test_unverified_inspection_never_queries_local_pid(monkeypatch):
    monkeypatch.setattr(inspection, 'host_pid_namespace', lambda: None)
    process = Mock()
    monkeypatch.setattr(inspection.psutil, 'Process', process)
    with pytest.raises(RuntimeError, match='unverified'):
        inspection.inspect(('environ', dict(pid=1, created=1, pid_namespace='elsewhere'), []))
    process.assert_not_called()


def test_environment_is_bounded_and_identity_revalidated(monkeypatch):
    monkeypatch.setattr(inspection, 'host_pid_namespace', lambda: 'host')
    process = Mock()
    process.create_time.return_value = 4
    process.is_running.return_value = True
    process.environ.return_value = {'A': 'value\x1b[2J', 'Z': 'x' * 70000}
    monkeypatch.setattr(inspection.psutil, 'Process', lambda pid: process)
    result = inspection.inspect(('environ', dict(pid=12, created=4, pid_namespace='host'), []))
    assert result['entries'][0] == 'A=value [2J'
    assert 'truncated' in result['entries'][-1]
    assert process.create_time.call_count == 2
    process.create_time.return_value = 5
    with pytest.raises(RuntimeError, match='reused'):
        inspection.inspect(('environ', dict(pid=12, created=4, pid_namespace='host'), []))


def test_inspector_discards_previous_page_response():
    inspector = inspection.Inspector()
    inspector.token = 'old'
    inspector.result = {'state': 'ok', 'entries': ['SECRET']}
    inspector.inflight = time.monotonic()
    connection = Mock()
    connection.poll.return_value = True
    connection.recv.return_value = ('old', inspector.result)
    inspector.connection = connection
    assert inspector.poll('new', None) is None
    assert inspector.inflight is None
    inspector.connection = None
    inspector.close()


def test_real_host_worker_rejects_unverified_request_and_closes():
    inspector = inspection.Inspector()
    try:
        target = dict(pid=os.getpid(), created=0, pid_namespace='unverified')
        end = time.monotonic() + 5
        result = None
        while result is None and time.monotonic() < end:
            start = time.monotonic()
            result = inspector.poll('request', ('environ', target, []))
            assert time.monotonic() - start < 0.5
            time.sleep(0.01)
        assert result and result['state'] == 'unavailable'
        child = inspector.process
        inspector.close()
        assert inspector.process is None and child._closed
    finally:
        inspector.close()


@pytest.mark.skipif(
    inspection.host_pid_namespace() is None, reason='verified Linux host namespace required'
)
def test_real_environment_and_metrics_for_owned_child():
    import subprocess
    import sys

    child = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        env=dict(os.environ, NPUTOP_TEST='owned-only'),
    )
    try:
        target = dict(
            pid=child.pid,
            created=psutil.Process(child.pid).create_time(),
            pid_namespace=inspection.host_pid_namespace(),
        )
        env = inspection.inspect(('environ', target, []))
        assert 'NPUTOP_TEST=owned-only' in env['entries']
        stats = inspection.inspect(('metrics', target, []))
        assert stats['ppid'] == os.getpid() and stats['rss'] >= 0
        tree = inspection.inspect(('tree', target, [dict(target, device='4:0')]))
        own = next(n for n in tree['nodes'] if n['pid'] == child.pid)
        assert own['devices'] == ['4:0']
        assert any(n['pid'] == os.getpid() for n in tree['nodes'])
    finally:
        child.terminate()
        child.wait(timeout=3)
