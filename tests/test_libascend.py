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
                'npu_id': 0,
                'chip_id': 0,
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
                'npu_id': 1,
                'chip_id': 0,
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
                'npu_id': 0,
                'chip_id': 0,
            }
        },
    ),
    (
        # npusmi_empty
        """
+------------------------------------------------------------------------------------------------+
| npu-smi 25.2.0                   Version: 25.2.0                                               |
+---------------------------+---------------+----------------------------------------------------+
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)|
| Chip  Phy-ID              | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        |
+===========================+===============+====================================================+
| 0     Ascend910           | OK            | 162.8       37                0    / 0             |
| 0     0                   | 0000:9C:00.0  | 0           0    / 0          3133 / 65536         |
+------------------------------------------------------------------------------------------------+
| 0     Ascend910           | OK            | -           37                0    / 0             |
| 1     1                   | 0000:9E:00.0  | 0           0    / 0          2876 / 65536         |
+===========================+===============+====================================================+
| 1     Ascend910           | OK            | 167.1       38                0    / 0             |
| 0     2                   | 0000:37:00.0  | 0           0    / 0          3116 / 65536         |
+------------------------------------------------------------------------------------------------+
| 1     Ascend910           | OK            | -           38                0    / 0             |
| 1     3                   | 0000:39:00.0  | 0           0    / 0          10568/ 65536         |
+===========================+===============+====================================================+
+---------------------------+---------------+----------------------------------------------------+
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      |
+===========================+===============+====================================================+
| No running processes found in NPU 0                                                            |
+===========================+===============+====================================================+
| 1       1                 | 990711        | python                   | 7746                    |
+===========================+===============+====================================================+
""",
        {
            0: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': 162800.0,
                'temp': 37,
                'procs': [],
                'bus_id': '0000:9C:00.0',
                'aicore': 0,
                'hbm_used': 3133 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.8, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 0,
            },
            1: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 37,
                'procs': [],
                'bus_id': '0000:9E:00.0',
                'aicore': 0,
                'hbm_used': 2876 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.4, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 1,
            },
            2: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': 167100.0,
                'temp': 38,
                'procs': [],
                'bus_id': '0000:37:00.0',
                'aicore': 0,
                'hbm_used': 3116 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.8, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 0,
            },
            3: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 38,
                'procs': [(990711, 7746 * 1024 * 1024)],
                'bus_id': '0000:39:00.0',
                'aicore': 0,
                'hbm_used': 10568 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=16.1, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 1,
            },
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
