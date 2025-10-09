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
import subprocess, re, time, sys
from collections import namedtuple
from types import ModuleType
from typing import Any
from collections.abc import Callable

# --------- 常量 ----------
NA            : str  = "N/A"
UINT_MAX      : int  = 0xFFFFFFFF
ULONGLONG_MAX : int  = 0xFFFFFFFFFFFFFFFF


# --------- 全局缓存 ----------
_CACHE      : dict[int, dict[str,Any]] = {}   # 物理 id ↦ 数据
_IDX        : list[int] = []                  # 逻辑 index ↦ 物理 id
_CACHE_TTL  = 0.8
_cache_ts   = 0.0

# --------- Regex ----------
_RE_L1 = re.compile(r"^\|\s*(\d+)\s+(\S+).*?\|\s*(\S+)\s+\|\s*([\d.]+)\s+(\d+)")
_RE_L2 = re.compile(r"^\|\s*[\d\s]+\|\s*([0-9A-Fa-f:.]+|NA)\s*\|\s*(\d+).*?\|$")
_RE_P  = re.compile(r"^\|\s*(\d+)\s+\d+\s+\|\s+(\d+)\s+\|.*?\|\s+(\d+)")

Util = namedtuple("UtilizationRates", ["npu", "mem", "bandwidth", "aicpu"])

def _update_cache(raw: str = None) -> None:
    global _cache_ts
    if time.time() - _cache_ts < _CACHE_TTL:
        return

    if not raw:
        raw = subprocess.run(
            ["npu-smi","info"], text=True, capture_output=True, timeout=3
        ).stdout
    raw = raw.splitlines()

    data: dict[int, dict[str,Any]] = {}
    
    raw_iter = iter(raw)
    for ln in raw_iter:
        ln = ln.strip()

        m1 = _RE_L1.match(ln)
        
        if m1:
            npu_id, name, ok, pwr, tmp = m1.groups()
            cur_id = int(npu_id)
            
            d = data.setdefault(cur_id, {})
            d.update(
                name=name, health=ok,
                power=float(pwr) * 1000,
                temp=int(tmp),
                procs=[]
            )
            
            try:
                ln_l2 = next(raw_iter).strip()
            except StopIteration:
                break 

            m2 = _RE_L2.match(ln_l2)
            
            if m2:
                bus, aic = m2.groups()
                pair = re.findall(r'(\d+)\s*/\s*(\d+)', ln_l2)[-1]
                h_used, h_tot = map(int, pair)
                d.update(
                    bus_id=bus,
                    aicore=int(aic),
                    hbm_used=h_used * 1024 * 1024,
                    hbm_total=h_tot * 1024 * 1024
                )

            continue

        mp = _RE_P.match(ln)
        if mp:
            npu_id, pid, mem = map(int, mp.groups())
            d = data.setdefault(npu_id, {})
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
    _cache_ts = time.time()

def _phys(idx: int) -> int|None:
    _update_cache()
    if 0 <= idx < len(_IDX):
        return _IDX[idx]
    return None

MemInfo  = namedtuple("MemoryInfo","total free used")
ProcInfo = namedtuple("Proc","pid usedNpuMemory")

def ascendDeviceGetCount() -> int:
    _update_cache(); return len(_IDX)

def ascendDeviceGetName(i:int):             id=_phys(i); return _CACHE.get(id,{}).get("name",NA)
def ascendDeviceGetTemperature(i:int):      id=_phys(i); return _CACHE.get(id,{}).get("temp",NA)
def ascendDeviceGetPowerUsage(i:int):       id=_phys(i); return _CACHE.get(id,{}).get("power",NA)
def ascendDeviceGetUtilizationRates(i:int): id=_phys(i); return _CACHE.get(id,{}).get("util",NA)

def ascendDeviceGetMemoryInfo(i:int):
    id=_phys(i)
    if id is None: return MemInfo(0,0,0)
    d=_CACHE.get(id,{})
    tot=d.get("hbm_total",0); used=d.get("hbm_used",0)
    return MemInfo(tot, tot-used, used)

def ascendDeviceGetProcessInfo(i:int):
    id=_phys(i)
    if id is None: return []
    return [ProcInfo(pid,mem) for pid,mem in _CACHE.get(id,{}).get("procs",[])]

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