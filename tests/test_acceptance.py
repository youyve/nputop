"""Community reports must not export workload identity or environment data."""

import json

from tools.acceptance import assess, compare
from test_monitor import fake_frame


def test_acceptance_redacts_process_identity_and_command():
    frame = fake_frame()
    frame['processes'] = [
        dict(
            pid=987654321,
            device='5:0',
            command='SECRET_COMMAND',
            user='SECRET_USER',
            memory=42,
            signal_allowed=True,
        )
    ]
    frame['host']['environment'] = dict(SECRET_KEY='SECRET_VALUE')
    report = assess(frame, None)
    text = json.dumps(report)
    assert all(
        secret not in text
        for secret in ('987654321', 'SECRET_COMMAND', 'SECRET_USER', 'SECRET_KEY', 'SECRET_VALUE')
    )
    assert report['process_check']['entries'] == 1
    assert report['acceptance'] == 'observed'
    result = compare(frame, frame)
    assert (
        result['topology_equal']
        and result['memory_capacity_equal']
        and result['process_membership_equal']
    )
    assert '987654321' not in json.dumps(result)
