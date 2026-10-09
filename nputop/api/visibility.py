# SPDX-License-Identifier: GPL-3.0-only
"""Explicit runtime-ID mappings, independent of display enumeration."""

import os
import re


def selection_error(frame, args):
    """Validate explicit device selection after enumeration, before rendering."""
    if frame is None:
        return None
    requested = getattr(args, 'only', None)
    if requested is not None:
        invalid = sorted(set(requested) - {d['id'] for d in frame['devices']})
        if invalid:
            return 'Invalid display device indices: ' + ', '.join(map(str, invalid))
    elif getattr(args, 'visible_ids', None) and any(
        d.get('logical_id') is None for d in frame['devices']
    ):
        return 'runtime device mapping unavailable; use --only with display IDs'
    return None


def visible_ids(environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get('ASCEND_RT_VISIBLE_DEVICES', environ.get('CUDA_VISIBLE_DEVICES'))
    if raw is None:
        return None
    if raw == '':
        return []
    if not re.fullmatch(r'[0-9]+(?:,[0-9]+)*', raw):
        raise ValueError(
            'Visible device IDs must be comma-separated non-negative integers without spaces'
        )
    ids = [int(value) for value in raw.split(',')]
    if ids != sorted(set(ids)):
        raise ValueError('ASCEND_RT_VISIBLE_DEVICES requires unique IDs in ascending order')
    return ids


def parse_mapping(text):
    """Read explicit logical IDs; older drivers may omit the physical-ID column."""
    header = re.search(
        r'^\s*NPU ID\s+Chip ID\s+Chip Logic ID(?P<physical>\s+Chip Phy-ID)?\s+Chip Name\s*$',
        text,
        re.M,
    )
    if header is None:
        return {}
    result = {}
    row_pattern = r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+'
    if header.group('physical'):
        row_pattern += r'(\d+|-)\s+'
    for line in text[header.end() :].splitlines():
        match = re.match(row_pattern, line)
        if match:
            card, chip, logical = map(int, match.groups()[:3])
            physical = match.group(4) if header.group('physical') else None
            physical = int(physical) if physical is not None and physical != '-' else None
            key = f'{card}:{chip}'
            if key in result:
                return {}  # Conflicting/duplicate topology must not authorize a filter.
            result[key] = dict(
                logical_id=logical, chip_physical_id=physical, id_source='npu-smi -m'
            )
    if len({r['logical_id'] for r in result.values()}) != len(result):
        return {}
    return result
