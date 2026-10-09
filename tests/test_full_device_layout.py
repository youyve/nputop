"""Expanded device rows keep model identity, metric semantics and legacy SMI layouts."""

from types import SimpleNamespace

import pytest

from nputop.api import monitor
from nputop.gui.library.widestring import WideString
from nputop.gui.monitor import Dashboard
from test_libascend import TEST_CASES
from test_libascend_a5 import A5_OUTPUT
from test_monitor import fake_frame, options


@pytest.mark.parametrize('width', [80, 120, 180])
@pytest.mark.parametrize('ascii_only', [False, True])
def test_full_device_rows_have_matching_headers_colors_and_one_model(width, ascii_only):
    value = fake_frame()
    row = value['devices'][0]
    row.update(logical_id=19, chip_physical_id=27, id_source='dcmi')
    row['metrics']['npu_overall'] = dict(value=82, state='ok')
    row['metrics']['aicore_mhz'] = dict(value=1250, state='ok')
    screen = Dashboard(options(monitor='full', ascii=ascii_only))
    lines = screen.lines(value, width, 60)
    device = [line for line in lines if line.target == ('devices', 0)]
    assert len(device) == 2
    identity, sensors = device
    assert identity.style == sensors.style == 'warning'
    assert sum(line.text.count('Fake910') for line in device) == 1
    assert 'Fake910' in identity.text and '5:0' in identity.text
    assert '0000:00:00.0' in identity.text
    assert '40C' in sensors.text and '120W / NA' in sensors.text and '20%' in sensors.text
    assert 'L:' not in identity.text and 'Phy:' not in identity.text
    assert not any('Model' in line.text for line in lines)
    edge = '|' if ascii_only else '│'
    titles = [line for line in lines if line.style == 'section'][:2]
    assert [part.strip() for part in titles[0].text.split(edge)[1:-1]] == [
        'ID  Name              Card:Chip',
        'Bus-Id',
        'Topology',
    ]
    assert [part.strip() for part in titles[1].text.split(edge)[1:-1]] == [
        'Temp             Pwr:Usage/Ref',
        'Memory used / total',
        'AICore',
    ]
    assert all(len(WideString(line.text)) <= width - 1 for line in lines)
    if width >= 120:
        assert 'MEM ' in identity.text and 'UTL ' not in identity.text
        assert 'UTL ' in sensors.text and '82%' in sensors.text and '@ 1250MHz' in sensors.text
        assert identity.spans[0][0] == sensors.spans[0][0] == 80
        assert [identity.spans[0][2], sensors.spans[0][2]] == ['warning', 'high']
    screen.expanded = True
    text = '\n'.join(line.text for line in screen.lines(value, width, 60))
    assert 'Display ID: 0' in text and 'Logical ID: 19' in text and 'Physical ID: 27' in text


@pytest.mark.parametrize(
    'raw,expected', TEST_CASES, ids=['910B2C', '310B4', '910C', '310P3', '910B3']
)
def test_legacy_smi_fixtures_survive_adapter_and_full_layout(monkeypatch, raw, expected):
    monkeypatch.setattr(monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=raw))
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 3)
    monkeypatch.setattr(monitor.libascend, '_DRIVER_VERSION', None)
    value = fake_frame('smi')
    value.update(monitor.SmiAdapter().sample())
    screen = Dashboard(options(monitor='full', ascii=False))
    lines = screen.lines(value, 180, 80)
    assert len(value['devices']) == len(expected)
    for display_id, (row, reference) in enumerate(zip(value['devices'], expected.values())):
        assert row['id'] == display_id
        assert row['key'] == f"{reference['npu_id']}:{reference['chip_id']}"
        assert row['model'] == reference['name'] and row['bus'] == reference['bus_id']
        metrics = row['metrics']
        assert metrics['memory_used']['value'] == reference['hbm_used']
        assert metrics['memory_total']['value'] == reference['hbm_total']
        assert metrics['aicore']['value'] == reference['aicore']
        assert metrics['temperature']['value'] == reference['temp']
        if isinstance(reference['power'], (int, float)):
            assert metrics['power']['value'] == reference['power'] / 1000
        else:
            assert metrics['power']['value'] is None and metrics['power']['state'] != 'ok'
        assert 'npu_overall' not in metrics
        assert row['logical_id'] is None and row['chip_physical_id'] is None
        device = [line for line in lines if line.target == ('devices', display_id)]
        assert len(device) == 2 and device[0].style == device[1].style
        text = '\n'.join(line.text for line in device)
        assert text.count(reference['name']) == 1 and row['key'] in text
        if '310P' in row['model']:
            assert 'UTL*' in text and str(reference['aicore']) + '%' in text
        else:
            assert text.split('UTL', 1)[1].split()[0] == '--'
        assert {
            (p['pid'], p['memory']) for p in value['processes'] if p['device'] == row['key']
        } == set(reference['procs'])
    if expected.get(3, {}).get('name') == '910B3':
        assert value['devices'][0]['key'] == '3:0' and value['devices'][0]['id'] == 0
        assert value['devices'][0]['metrics']['memory_total']['value'] == 64 * 1024**3
        assert value['process_status'] == 'ok' and value['processes'] == []
        screen.expanded = True
        details = '\n'.join(line.text for line in screen.lines(value, 180, 80))
        assert 'Logical ID: --' in details and 'Physical ID: --' in details
        assert 'Mapping: unavailable' in details


def test_overall_only_smi_header_does_not_claim_aicore(monkeypatch):
    monkeypatch.setattr(
        monitor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=A5_OUTPUT)
    )
    monkeypatch.setattr(monitor.libascend, '_smi_timeout', lambda: 10)
    value = fake_frame('smi')
    value.update(monitor.SmiAdapter().sample())
    lines = Dashboard(options(monitor='full')).lines(value, 180, 80)
    assert any('NPU%' in line.text for line in lines if line.style == 'section')
    assert not any('AICore' in line.text for line in lines if line.style == 'section')
    assert any(
        'UTL ' in line.text and '20%' in line.text
        for line in lines
        if line.target == ('devices', 0)
    )
