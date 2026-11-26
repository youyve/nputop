from pathlib import Path

import pytest

from nputop.api import cann, libascend, monitor
from test_monitor import fake_frame


@pytest.fixture(autouse=True)
def isolate_installations(monkeypatch, tmp_path):
    monkeypatch.delenv('ASCEND_HOME_PATH', raising=False)
    monkeypatch.delenv('ASCEND_TOOLKIT_HOME', raising=False)
    monkeypatch.setattr(cann.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(cann.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(cann, '_DEFAULT_PREFIX', tmp_path / 'default')


def install_info(root, version):
    path = root / 'ascend_toolkit_install.info'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('innerversion=V100R001\nversion = ' + version + '\n', encoding='utf-8')
    return path


def test_both_ui_paths_prefer_active_installation(monkeypatch, tmp_path):
    active = tmp_path / 'custom install'
    alternate = tmp_path / 'other'
    install_info(active / 'aarch64-linux', '9.2.0')
    install_info(alternate / 'latest', '8.1.RC1')
    install_info(cann._DEFAULT_PREFIX / 'cann' / 'aarch64-linux', '8.5.0')
    monkeypatch.setenv('ASCEND_HOME_PATH', str(active))
    monkeypatch.setenv('ASCEND_TOOLKIT_HOME', str(alternate))

    assert monitor.cann_version() == libascend.ascendSystemGetCANNVersion() == '9.2.0'
    manager = monitor.BackendManager()
    assert manager.cann == '9.2.0'
    assert manager.cann_detection == {'source': 'ASCEND_HOME_PATH', 'failures': []}


def test_unusable_environment_tries_next_source_without_exposing_paths(monkeypatch, tmp_path):
    denied = install_info(tmp_path / 'private' / 'aarch64-linux', '8.1.0')
    read_text = Path.read_text

    def read_file(path, *args, **kwargs):
        if path == denied:
            raise PermissionError(str(path))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_file)
    monkeypatch.setenv('ASCEND_HOME_PATH', str(denied.parent.parent))
    monkeypatch.setenv('ASCEND_TOOLKIT_HOME', str(tmp_path / 'missing'))
    install_info(cann._DEFAULT_PREFIX / 'ascend-toolkit' / 'latest' / 'aarch64-linux', '8.0.RC3')

    version, detection = cann.detect_cann_version()
    report = monitor.diagnostic_report(dict(fake_frame(), cann=version, cann_detection=detection))
    assert report['cann'] == '8.0.RC3'
    assert report['cann_detection'] == {
        'source': 'default',
        'failures': [
            {'source': 'ASCEND_HOME_PATH', 'reasons': ['permission']},
            {'source': 'ASCEND_TOOLKIT_HOME', 'reasons': ['not-found']},
        ],
    }
    assert str(tmp_path) not in repr(report)


def test_architecture_directory_and_invalid_version(monkeypatch, tmp_path):
    monkeypatch.setattr(cann.platform, 'machine', lambda: 'x86_64')
    root = tmp_path / 'prefix'
    monkeypatch.setenv('ASCEND_TOOLKIT_HOME', str(root))
    path = install_info(root / 'cann' / 'x86_64-linux', 'invalid')
    install_info(root / 'cann' / 'aarch64-linux', '8.0.0')

    assert cann.detect_cann_version() == (
        None,
        {
            'source': None,
            'failures': [
                {'source': 'ASCEND_TOOLKIT_HOME', 'reasons': ['invalid-version']},
                {'source': 'default', 'reasons': ['not-found']},
            ],
        },
    )
    assert libascend.ascendSystemGetCANNVersion() == libascend.NA
    path.unlink()
    install_info(root / 'cann', '9.2.0')
    assert cann.detect_cann_version() == ('9.2.0', {'source': 'ASCEND_TOOLKIT_HOME', 'failures': []})
