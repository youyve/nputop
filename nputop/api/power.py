# SPDX-License-Identifier: Apache-2.0
"""Conservative, explicitly estimated power references for the native UI.

These are not enforced power limits. See the README's power section for the
unit/scope assumptions and the hardware observations behind them.
"""

from __future__ import annotations


# Ascend/mind-cluster ascend-common/devmanager/common/constants.go, commit
# 3779de1d: A900/A9000 A3 Bin1/2/3 and A800I A3. Chip count alone is not evidence.
A3_BOARD_IDS = frozenset((0xB0, 0xB1, 0xB2, 0xB3, 0xB4))


def power_reference(model, estimates, board_id=None, telemetry=None):
    name = str(model).replace('Ascend', '').strip().upper()
    shared = board_id in A3_BOARD_IDS or name == '910C'
    scope = 'card-shared' if shared else 'unknown'
    result = {
        'value_w': None,
        'estimated': True,
        'scope': scope,
        'source': None,
        'reason': 'No supported rated-power reading or model estimate',
        'raw_unit_assumption': None,
        'query': dict(telemetry) if telemetry else None,
    }
    if telemetry:
        raw = telemetry.get('raw')
        # The A2/A3 documentation gives 150000..600000 without a unit. A3 on
        # the tested firmware reports 949200. Treat mW as a hypothesis; allow
        # a broad A3 sanity bound, not an asserted hardware maximum. Unknown
        # families never inherit this interpretation just by returning a uint.
        recognized = shared or name.startswith('910')
        upper = 2000000 if shared else 600000
        valid = (
            recognized
            and telemetry.get('state') == 'ok'
            and isinstance(raw, int)
            and not isinstance(raw, bool)
            and 150000 <= raw <= upper
        )
        if valid:
            result.update(
                value_w=raw / 1000.0,
                source='dcmi-rated-inferred',
                raw_unit_assumption='mW',
                reason='Rated reference; mW inferred, not an enforced limit'
                + ('; card scope inferred for A3' if shared else '; scope unverified'),
            )
            return result
        result['reason'] = 'Rated query unavailable, unsupported family, or implausible raw value'
    # Keep the historical table as an exact-model fallback. The old 910C=350 W
    # entry has no defensible dual-die scope, so it remains legacy-API-only.
    fallback = estimates.get(name) if not shared else None
    if isinstance(fallback, (int, float)) and fallback > 0:
        result.update(
            value_w=float(fallback),
            source='model-estimate',
            reason='Historical model estimate; scope unverified, not an enforced limit',
        )
    return result
