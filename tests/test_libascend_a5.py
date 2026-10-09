from types import SimpleNamespace

import pytest

from nputop.api import libascend


# Synthetic data: only the table layout and chip name represent A5 output.
A5_OUTPUT = """
| npu-smi 99.0.rc1.b1    Version: 99.0.rc1.b1 |
| NPU ID | Name        | Health | Power(W) Temp(C) Hugepages-Usage(page) |
|        |             | Bus-Id | NPU Util(%) Memory-Usage(MB) HBM-Usage(MB) |
| 0      | Ascend950DT | OK     | 100.0 40 0 / 0 |
|        |             | NA     | 20 0 / 0 1024 / 8192 |
| 3      | Ascend950DT | Alarm  | 120.0 45 0 / 0 |
|        |             | NA     | 0 0 / 0 2048 / 8192 |
| NPU ID | Process id | Process name | Process memory(MB) | Process id in container |
| 0      | 1001       | python       | 256                | NA                      |
| 0      | 1002       | python       | 512                | 101                     |
| No running processes found in NPU 3 |
"""


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch):
    monkeypatch.setattr(libascend, '_CACHE', {})
    monkeypatch.setattr(libascend, '_IDX', [])
    monkeypatch.setattr(libascend, '_npu_chip_phy', {})
    monkeypatch.setattr(libascend, '_cache_ts', 0.0)
    monkeypatch.setattr(libascend, '_DRIVER_VERSION', None)
    monkeypatch.setattr(libascend, '_SMI_TIMEOUT', None)


def test_a5_parse():
    libascend._update_cache(A5_OUTPUT)

    assert libascend._IDX == [0, 3]
    assert libascend._npu_chip_phy == {(0, 0): 0, (3, 0): 3}
    assert libascend.ascendSystemGetDriverVersion() == '99.0.rc1.b1'
    assert libascend._CACHE[0] == {
        'name': 'Ascend950DT', 'health': 'OK', 'power': 100000.0, 'temp': 40,
        'npu_id': 0, 'chip_id': 0, 'bus_id': 'NA', 'aicore': 20,
        'hbm_used': 1024 * 1024**2, 'hbm_total': 8192 * 1024**2,
        'procs': [(1001, 256 * 1024**2), (1002, 512 * 1024**2)],
        'util': libascend.Util(20, 12.5, 'N/A', 'N/A'),
    }
    assert libascend._CACHE[3]['health'] == 'Alarm'
    assert libascend._CACHE[3]['hbm_used'] == 2048 * 1024**2
    assert libascend._CACHE[3]['procs'] == []


def test_a5_refresh():
    libascend._update_cache(A5_OUTPUT)
    updated = A5_OUTPUT.replace('1024 / 8192', '3072 / 8192')
    updated = updated.replace('20 0 / 0', '60 0 / 0')
    updated = '\n'.join(line for line in updated.splitlines() if '1001' not in line)
    updated += '\n| 3 | 1003 | python | 128 | NA |'
    # An unknown device in the process table must not create a device.
    updated += '\n| 9 | 1004 | python | 128 | NA |'
    libascend._cache_ts = 0.0

    libascend._update_cache(updated)

    assert libascend._IDX == [0, 3]
    assert libascend.ascendDeviceGetMemoryInfo(0).used == 3072 * 1024**2
    assert libascend.ascendDeviceGetUtilizationRates(0).npu == 60
    assert libascend.ascendDeviceGetProcessInfo(0) == [libascend.ProcInfo(1002, 512 * 1024**2)]
    assert libascend.ascendDeviceGetProcessInfo(1) == [libascend.ProcInfo(1003, 128 * 1024**2)]


@pytest.mark.parametrize(
    'chip_name,timeout', [('Ascend950DT', 10.0), ('Ascend910B', 3.0), ('Ascend910', 3.0)],
)
def test_timeout_detected_once(monkeypatch, chip_name, timeout):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs['timeout']))
        if command == ['npu-smi', 'info', '-m']:
            return SimpleNamespace(stdout=(
                'NPU ID Slot ID Chip ID Chip Phy-ID Chip Name\n'
                f'0 0 0 0 {chip_name}\n'
            ))
        return SimpleNamespace(stdout=A5_OUTPUT)

    monkeypatch.setattr(libascend.subprocess, 'run', run)
    libascend._update_cache()
    libascend._cache_ts = 0.0
    libascend._update_cache()

    assert calls == [
        (['npu-smi', 'info', '-m'], 3.0),
        (['npu-smi', 'info'], timeout),
        (['npu-smi', 'info'], timeout),
    ]


@pytest.mark.parametrize('failure', ['missing', 'unsupported', 'timeout', 'empty'])
def test_detection_failure_keeps_default(monkeypatch, failure):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs['timeout']))
        if failure == 'missing':
            raise FileNotFoundError('npu-smi')
        if failure == 'unsupported':
            raise libascend.subprocess.CalledProcessError(1, command)
        if failure == 'timeout':
            raise libascend.subprocess.TimeoutExpired(command, 3)
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(libascend.subprocess, 'run', run)

    assert libascend._smi_timeout() == 3.0
    assert libascend._smi_timeout() == 3.0
    assert calls == [(['npu-smi', 'info', '-m'], 3.0)]
