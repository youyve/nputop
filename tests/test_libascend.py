import time

import pytest

from nputop.api import libascend


# (raw_output, expected_cache)
TEST_CASES = [
    (
        # npusmi_hbm
        """
+------------------------------------------------------------------------------------------------+ 
| npu-smi 23.0.2.1                 Version: 23.0.2.1                                             | 
+---------------------------+---------------+----------------------------------------------------+ 
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)| 
| Chip                      | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        | 
+===========================+===============+====================================================+ 
| 0     910B2C              | OK            | 88.6        51                0    / 0             | 
| 0                         | 0000:5A:00.0  | 0           0    / 0          20701/ 65536         | 
+===========================+===============+====================================================+ 
| 1     910B2C              | OK            | 99.6        50                0    / 0             | 
| 0                         | 0000:19:00.0  | 0           0    / 0          20687/ 65536         | 
+===========================+===============+====================================================+ 
+---------------------------+---------------+----------------------------------------------------+ 
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      | 
+===========================+===============+====================================================+ 
| 0       0                 | 124528        | python3.8                | 17400                   | 
+---------------------------+---------------+----------------------------------------------------+ 
""",
        {
            0: {
                'name': '910B2C',
                'health': 'OK',
                'power': 88600.0,
                'temp': 51,
                'procs': [(124528, 18245222400)],
                'bus_id': '0000:5A:00.0',
                'aicore': 0,
                'hbm_used': 21706571776,
                'hbm_total': 68719476736,
                'util': libascend.Util(npu=0, mem=31.6, bandwidth='N/A', aicpu='N/A'),
            },
            1: {
                'name': '910B2C',
                'health': 'OK',
                'power': 99600.0,
                'temp': 50,
                'procs': [],
                'bus_id': '0000:19:00.0',
                'aicore': 0,
                'hbm_used': 21691891712,
                'hbm_total': 68719476736,
                'util': libascend.Util(npu=0, mem=31.6, bandwidth='N/A', aicpu='N/A'),
            },
        },
    ),
    (
        # npusmi_nohbm
        """
+--------------------------------------------------------------------------------------------------------+ 
| npu-smi 23.0.0                                   Version: 23.0.0                                       | 
+-------------------------------+-----------------+------------------------------------------------------+ 
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) | 
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        | 
+===============================+=================+======================================================+ 
| 0       310B4                 | Alarm           | 0.0          65                15    / 15            | 
| 0       0                     | NA              | 0            3628 / 15609                            | 
+===============================+=================+======================================================+ 
""",
        {
            0: {
                'name': '310B4',
                'health': 'Alarm',
                'power': 0.0,
                'temp': 65,
                'procs': [],
                'bus_id': 'NA',
                'aicore': 0,
                'hbm_used': 3804233728,
                'hbm_total': 16367222784,
                'util': libascend.Util(npu=0, mem=23.2, bandwidth='N/A', aicpu='N/A'),
            }
        },
    ),
]


@pytest.mark.parametrize("raw,expected_cache", TEST_CASES)
def test_npusmi_parse(raw, expected_cache):
    libascend._CACHE.clear()
    libascend._IDX.clear()
    time.sleep(1)

    libascend._update_cache(raw)

    assert list(expected_cache.keys()) == libascend._IDX

    for key, expected_val in expected_cache.items():
        assert key in libascend._CACHE
        cached_val = libascend._CACHE[key]
        for field, value in expected_val.items():
            assert cached_val[field] == value
