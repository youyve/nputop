# This file is part of nputop, the interactive Ascend-NPU process viewer.
#
# Copyright (c) 2025 Xuehai Pan <XuehaiPan@pku.edu.cn>
# Copyright (c) 2025 Lianzhong You <youlianzhong@gml.ac.cn>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations
import subprocess, re, time, sys, threading
from collections import namedtuple
from types import ModuleType
from typing import Any
from collections.abc import Callable
import platform

from nputop.api import libdcmi

# --------- 常量 ----------
NA            : str  = "N/A"
UINT_MAX      : int  = 0xFFFFFFFF
ULONGLONG_MAX : int  = 0xFFFFFFFFFFFFFFFF


# --------- 全局缓存 ----------
_CACHE      : dict[int, dict[str,Any]] = {}   # 物理 id ↦ 数据
_IDX        : list[int] = []                  # 逻辑 index ↦ 物理 id
_CACHE_TTL  = 0.8
_cache_ts   = 0.0
_CACHE_LOCK = threading.RLock()
_DRIVER_VERSION = None
_POWER_LIMIT = {
    "310": None,
    "310B": None,
    "310P1": 90,
    "310P3": 72,
    "910A": 310,
    "910B": 265,
    "910B1": 430,
    "910B2": 420,
    "910B3": 350,
    "910C": 350,
}
_npu_chip_phy : dict[tuple[int, int], int] = {} # (npu id, chip_id) ↦ phy id
_DCMI_BACKEND = None
_DCMI_ATTEMPTED = False
# --------- Regex ----------
_RE_L1 = re.compile(r"^\|\s*(\d+)\s+(\S+).*?\|\s*(\S+)\s+\|\s*(\S+)\s+(\d+)")
_RE_L2 = re.compile(r"^\|\s*(\d+)\s+(\d*)\s*\|\s*([0-9A-Fa-f:.]+|NA)\s*\|\s*(\d+).*?\|$")
_RE_P  = re.compile(r"^\|\s*(\d+)\s+(\d+)\s+\|\s+(\d+)\s+\|.*?\|\s+(\d+)")
_RE_R = re.compile(r"^\|\s*(\S+)\s+([\d.rcRC]+)\s+Version:\s*([\d.rcRC]+)")

Util = namedtuple("UtilizationRates", ["npu", "mem", "bandwidth", "aicpu"])
ClockInfos = libdcmi.ClockInfos
ClockSpeedInfos = libdcmi.ClockSpeedInfos
ThroughputInfo = libdcmi.ThroughputInfo
DvppUtilization = libdcmi.DvppUtilization
HbmInfo = libdcmi.HbmInfo


def _get_dcmi_backend():
    """Return the initialized DCMI backend, or ``None`` for the fallback path."""

    global _DCMI_ATTEMPTED
    global _DCMI_BACKEND
    if _DCMI_ATTEMPTED:
        return _DCMI_BACKEND

    _DCMI_ATTEMPTED = True
    try:
        _DCMI_BACKEND = libdcmi.create_backend()
    except Exception:  # DCMI is an optional system dependency.
        _DCMI_BACKEND = None
    return _DCMI_BACKEND


def _reset_dcmi_backend() -> None:
    """Reset lazy backend state (primarily useful for tests)."""

    global _DCMI_ATTEMPTED
    global _DCMI_BACKEND
    _DCMI_ATTEMPTED = False
    _DCMI_BACKEND = None

def _update_cache(raw: str = None) -> None:
    global _cache_ts
    global _DRIVER_VERSION
    if raw is None and time.time() - _cache_ts < _CACHE_TTL:
        return

    with _CACHE_LOCK:
        if raw is None and time.time() - _cache_ts < _CACHE_TTL:
            return

        if not raw:
            raw = subprocess.run(
                ["npu-smi","info"], text=True, capture_output=True, timeout=3
            ).stdout
        raw = raw.splitlines()

        data: dict[int, dict[str,Any]] = {}
        chip_phy: dict[tuple[int, int], int] = {}
        
        raw_iter = iter(raw)
        next(raw_iter)
        ln_l0 = next(raw_iter).strip()
        if _DRIVER_VERSION is None:
            m0 = _RE_R.match(ln_l0)
            if m0:
                _,_DRIVER_VERSION,_ = m0.groups()
        for ln in raw_iter:
            ln = ln.strip()

            m1 = _RE_L1.match(ln)
            
            if m1:
                npu_id, name, ok, pwr, tmp = m1.groups()
                cur_id = int(npu_id)

                ln1_data = dict(
                    name=name, health=ok,
                    power=float(pwr) * 1000 if (pwr != '-' and pwr != "NA") else NA + " ",
                    temp=int(tmp),
                    procs=[],
                    npu_id=cur_id,
                    chip_id=0,
                )

                try:
                    ln_l2 = next(raw_iter).strip()
                except StopIteration:
                    d = data.setdefault(cur_id, {})
                    d.update(ln1_data)
                    chip_phy[(d['npu_id'], d['chip_id'])] = cur_id
                    break

                m2 = _RE_L2.match(ln_l2)

                if m2:
                    chip_id, phy_id, bus, aic = m2.groups()
                    chip_id_kwargs = {'chip_id': int(chip_id)} if chip_id else {}

                    if phy_id:
                        cur_id = int(phy_id)
                    d = data.setdefault(cur_id, {})
                    d.update(ln1_data)

                    d.update(
                        bus_id=bus,
                        aicore=int(aic),
                        **chip_id_kwargs,
                    )
                    pairs = re.findall(r'(\d+)\s*/\s*(\d+)', ln_l2)
                    if pairs:
                        h_used, h_tot = map(int, pairs[-1])
                        d.update(
                            hbm_used=h_used * 1024 * 1024,
                            hbm_total=h_tot * 1024 * 1024,
                        )
                    chip_phy[(d['npu_id'], d['chip_id'])] = cur_id
                else:
                    d = data.setdefault(cur_id, {})
                    d.update(ln1_data)
                    chip_phy[(d['npu_id'], d['chip_id'])] = cur_id

                continue

            mp = _RE_P.match(ln)
            if mp:
                npu_id, chip_id, pid, mem = map(int, mp.groups())
                phy_id = chip_phy.get((npu_id, chip_id))
                if phy_id is None:
                    continue
                d = data.setdefault(phy_id, {})
                d.setdefault("procs", []).append((pid, mem * 1024 * 1024))

        for d in data.values():
            d.setdefault("power", NA); d.setdefault("temp", NA)
            d.setdefault("aicore", NA)
            d.setdefault("hbm_used", 0); d.setdefault("hbm_total", 0)
            d.setdefault("procs", [])
            mem_pct = (round(100*d["hbm_used"]/d["hbm_total"],1)
                       if d["hbm_total"] else NA)
            d["util"] = Util(d["aicore"], mem_pct, NA, NA)

        _CACHE.clear(); _CACHE.update(data)
        _IDX.clear();   _IDX.extend(sorted(_CACHE.keys()))
        _npu_chip_phy.clear(); _npu_chip_phy.update(chip_phy)
        _cache_ts = time.time()

def _phys(idx: int) -> int|None:
    _update_cache()
    if 0 <= idx < len(_IDX):
        return _IDX[idx]
    return None

MemInfo  = namedtuple("MemoryInfo","total free used")
ProcInfo = namedtuple("Proc","pid usedNpuMemory")

def ascendDeviceGetCount() -> int:
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.count()
        except Exception:
            return 0
    _update_cache(); return len(_IDX)

def ascendDeviceGetUUID(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.uuid(i) or NA
        except Exception:
            return NA
    return f"ASCEND-{i:02d}"


def ascendDeviceGetBusId(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.bus_id(i)
        except Exception:
            return NA
    id = _phys(i)
    return _CACHE.get(id, {}).get("bus_id", NA)


def ascendDeviceGetName(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.name(i)
        except Exception:
            return NA
    id = _phys(i)
    return _CACHE.get(id, {}).get("name", NA)


def ascendDeviceGetTemperature(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.temperature(i)
        except Exception:
            return NA
    id = _phys(i)
    return _CACHE.get(id, {}).get("temp", NA)


def ascendDeviceGetPowerUsage(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.power_usage(i)
        except Exception:
            return NA
    id = _phys(i)
    return _CACHE.get(id, {}).get("power", NA)


def ascendDeviceGetUtilizationRates(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return Util(*backend.utilization_rates(i))
        except Exception:
            return NA
    id = _phys(i)
    return _CACHE.get(id, {}).get("util", NA)


def ascendDeviceGetEncoderUtilization(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.dvpp_utilization(i).encoder
        except Exception:
            return NA
    return NA


def ascendDeviceGetDecoderUtilization(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.dvpp_utilization(i).decoder
        except Exception:
            return NA
    return NA


def ascendDeviceGetDvppUtilization(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.dvpp_utilization(i)
        except Exception:
            pass
    return DvppUtilization(NA, NA)


def ascendDeviceGetClockInfos(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.clock_infos(i)
        except Exception:
            pass
    return ClockInfos(NA, NA, NA, NA)


def ascendDeviceGetMaxClockInfos(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.max_clock_infos(i)
        except Exception:
            pass
    return ClockInfos(NA, NA, NA, NA)


def ascendDeviceGetClockSpeedInfos(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.clock_speed_infos(i)
        except Exception:
            pass
    return ClockSpeedInfos(ClockInfos(NA, NA, NA, NA), ClockInfos(NA, NA, NA, NA))


def ascendDeviceGetFanSpeed(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.fan_speed(i)
        except Exception:
            return NA
    return NA


def ascendDeviceGetPcieThroughput(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.pcie_throughput(i)
        except Exception:
            pass
    return ThroughputInfo(NA, NA)


def ascendDeviceGetPcieTxThroughput(i: int):
    return ascendDeviceGetPcieThroughput(i).tx


def ascendDeviceGetPcieRxThroughput(i: int):
    return ascendDeviceGetPcieThroughput(i).rx


def ascendDeviceGetEccErrors(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.total_volatile_uncorrected_ecc_errors(i)
        except Exception:
            return NA
    return NA

def ascendDeviceGetMemoryInfo(i:int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return MemInfo(*backend.memory_info(i))
        except Exception:
            return MemInfo(NA, NA, NA)
    id=_phys(i)
    if id is None: return MemInfo(0,0,0)
    d=_CACHE.get(id,{})
    tot=d.get("hbm_total",0); used=d.get("hbm_used",0)
    return MemInfo(tot, tot-used, used)


def ascendDeviceGetHbmInfo(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.hbm_info(i)
        except Exception:
            pass
    return HbmInfo(NA, NA, NA, NA, NA)


def ascendDeviceGetHbmFrequency(i: int):
    return ascendDeviceGetHbmInfo(i).frequency


def ascendDeviceGetHbmTemperature(i: int):
    return ascendDeviceGetHbmInfo(i).temperature


def ascendDeviceGetHbmBandwidth(i: int):
    return ascendDeviceGetHbmInfo(i).bandwidth


def ascendDeviceGetMemoryBandwidthUtilization(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.utilization_rates(i).bandwidth
        except Exception:
            return NA
    return NA


def ascendDeviceGetAicpuUtilization(i: int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.utilization_rates(i).aicpu
        except Exception:
            return NA
    return NA

def ascendDeviceGetProcessInfo(i:int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return tuple(ProcInfo(p.pid, p.usedNpuMemory) for p in backend.process_info(i))
        except Exception:
            return ()
    id=_phys(i)
    if id is None: return []
    return [ProcInfo(pid,mem) for pid,mem in _CACHE.get(id,{}).get("procs",[])]

def ascendSystemGetDriverVersion() -> str:
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            return backend.driver_version()
        except Exception:
            return NA
    global _DRIVER_VERSION
    return _DRIVER_VERSION or NA

def ascendSystemGetCANNVersion() -> str:
    arch = platform.machine()
    arch_subdir_map = {"x86_64": "x86_64-linux", "aarch64": "aarch64-linux"}
    arch_subdir = arch_subdir_map.get(arch)
    try:
        result = subprocess.run(['cat', f'/usr/local/Ascend/ascend-toolkit/latest/{arch_subdir}/ascend_toolkit_install.info'], 
                              capture_output=True, text=True, check=True)
        output = result.stdout
        
        match = re.search(r'version\s*=\s*([\w.+-]+)', output)
        return match.group(1) if match else NA
            
    except (FileNotFoundError, PermissionError):
        return NA
    
def ascendDeviceGetPowerLimit(i:int):
    backend = _get_dcmi_backend()
    if backend is not None:
        try:
            chip_name = backend.name(i)
        except Exception:
            return NA
        return _POWER_LIMIT.get(chip_name, NA)
    id=_phys(i)
    if id is None: return NA
    chip_name = _CACHE.get(id,{}).get("name")
    return _POWER_LIMIT.get(chip_name, NA)

def nvmlCheckReturn(v:Any, t:type|tuple[type,...]|None=None)->bool:
    return v != NA and (isinstance(v,t) if t else True)

def nvmlQuery(func:Callable|str,*a,default:Any=NA,**kw)->Any:
    try:
        f = globals()[func] if isinstance(func,str) else func
        return f(*a,**kw)
    except Exception:
        return default

VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")

class _Mod(ModuleType):
    def __getattr__(self,n): return globals()[n]
    def __enter__(self): return self
    def __exit__(self,*exc): ...
sys.modules[__name__].__class__ = _Mod
