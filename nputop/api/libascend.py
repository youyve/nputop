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


# nputop/api/libascend.py  ——  Ascend “伪-NVML” ultra-light layer  v14
from __future__ import annotations
import atexit, subprocess, re, time, sys
from collections import namedtuple
from types import ModuleType
from typing import Any, Callable

NA            : str  = "N/A"
UINT_MAX      : int  = 0xFFFFFFFF
ULONGLONG_MAX : int  = 0xFFFFFFFFFFFFFFFF


# ----------------- FAST GLOBAL CACHE -----------------
_CACHE      : dict[int, dict[str,Any]] = {}   # 物理 id → 数据
VISIBLE     : list[int]               = []     # 逻辑 idx → 物理 id
_CACHE_T    = 0.0
_TTL        = 0.8

_RE_L1  = re.compile(r"^\|\s*(\d+)\s+(\S+).*?\|\s*(\S+)\s+\|\s*([\d.]+)\s+(\d+)")
_RE_L2  = re.compile(r"^\|\s*\d+\s+\|\s+([0-9A-Fa-f:.]+).*?\|\s+(\d+)")
_RE_PR  = re.compile(r"^\|\s*(\d+)\s+\d+\s+\|\s+(\d+)\s+\|")
_UPAIR  = re.compile(r'(\d+)\s*/\s*(\d+)')     # 匹配 “num / num”

def _update_cache() -> None:
    global _CACHE_T
    if time.time() - _CACHE_T < _TTL:
        return

    txt = subprocess.run(["npu-smi","info"], text=True,
                         capture_output=True, timeout=3).stdout.splitlines()

    data: dict[int, dict[str,Any]] = {}
    cur = -1
    for ln in txt:
        m1 = _RE_L1.match(ln)
        if m1:
            cur = int(m1.group(1))
            d = data.setdefault(cur,{})
            d.update(name=m1.group(2), health=m1.group(3),
                     power=float(m1.group(4))*1000,  # W→mW
                     temp=int(m1.group(5)))
            continue

        m2 = _RE_L2.match(ln)
        if m2 and cur >= 0:
            pairs = _UPAIR.findall(ln)
            used, tot = map(int, pairs[-1]) if pairs else (0,0)
            d = data.setdefault(cur,{})
            d.update(bus_id=m2.group(1),
                     aicore=int(m2.group(2)),
                     hbm_used = used*1024*1024,
                     hbm_total= tot *1024*1024)
            continue

        mp = _RE_PR.match(ln)
        if mp:
            npu,pid,mem = map(int, mp.groups())
            data.setdefault(npu,{}).setdefault("procs",[]).append((pid,mem*1024*1024))

    Util = namedtuple("UtilizationRates","npu mem bandwidth aicpu")
    for d in data.values():
        d.setdefault("aicore",NA)
        d.setdefault("hbm_used",0); d.setdefault("hbm_total",0)
        pct = round(100*d["hbm_used"]/d["hbm_total"],1) if d["hbm_total"] else NA
        d["util"] = Util(d["aicore"], pct, NA, NA)
        d.setdefault("procs",[])

    _CACHE.clear(); _CACHE.update(data)
    VISIBLE[:] = sorted(data)           # 更新逻辑索引表
    _CACHE_T = time.time()

def _phys(idx:int)->int:                # 逻辑 idx → 物理 id
    if VISIBLE and 0 <= idx < len(VISIBLE):
        return VISIBLE[idx]
    return idx

# ----------------- NVML-like 查询接口 -----------------
MemInfo  = namedtuple("MemoryInfo","total free used")
ProcInfo = namedtuple("Proc","pid usedNpuMemory")

def ascendDeviceGetCount():             _update_cache(); return len(VISIBLE or _CACHE)
def ascendDeviceGetName(i):             _update_cache(); return _CACHE.get(_phys(i),{}).get("name",NA)
def ascendDeviceGetTemperature(i):      _update_cache(); return _CACHE.get(_phys(i),{}).get("temp",NA)
def ascendDeviceGetPowerUsage(i):       _update_cache(); return _CACHE.get(_phys(i),{}).get("power",NA)
def ascendDeviceGetUtilizationRates(i): _update_cache(); return _CACHE.get(_phys(i),{}).get("util",NA)

def ascendDeviceGetMemoryInfo(i):
    _update_cache()
    d = _CACHE.get(_phys(i),{})
    tot=d.get("hbm_total",0); used=d.get("hbm_used",0)
    return MemInfo(tot, tot-used, used)

def ascendDeviceGetProcessInfo(i):
    _update_cache()
    return [ProcInfo(pid,mem) for pid,mem in _CACHE.get(_phys(i),{}).get("procs",[])]

# ------------ tiny helpers (NVML 兼容) ------------
def nvmlCheckReturn(v:Any,t:type|tuple[type,...]|None=None)->bool:
    return v is not NA and (isinstance(v,t) if t else True)

def nvmlQuery(func:Callable|str,*a,default:Any=NA,**kw)->Any:
    try: f = globals()[func] if isinstance(func,str) else func; return f(*a,**kw)
    except Exception: return default

VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")

# ----- context-manager friendly module -----
class _Mod(ModuleType):
    def __getattr__(self,n): return globals()[n]
    def __enter__(self): return self
    def __exit__(self,*exc): ...
sys.modules[__name__].__class__ = _Mod
