"""Keep offline tests offline, including when they run on an NPU server."""

import pytest

from nputop.api import libascend


@pytest.fixture(autouse=True)
def isolate_optional_backend(monkeypatch):
    monkeypatch.setattr(libascend, '_DCMI_BACKEND', None)
    monkeypatch.setattr(libascend, '_DCMI_ATTEMPTED', True)
