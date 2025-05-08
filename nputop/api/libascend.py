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
import atexit, subprocess, re, threading, time, sys
from collections import namedtuple
from types import ModuleType
from typing import Any, Callable, TypeAlias

NA            : str  = "N/A"
UINT_MAX      : int  = 0xFFFFFFFF
ULONGLONG_MAX : int  = 0xFFFFFFFFFFFFFFFF
c_aclDevice_t : TypeAlias = int        # 占位

import acl
_init_lock = threading.Lock(); _acl_inited = False
def _ensure_acl(dev: int|None=None):
    global _acl_inited
    with _init_lock:
        if not _acl_inited:
            acl.init(); _acl_inited = True; atexit.register(lambda: acl.finalize())
        if dev is not None:
            try: acl.rt.set_device(dev)
            except Exception: pass

# ----------------- FAST GLOBAL CACHE -----------------
_CACHE        : dict[int, dict[str, Any]] = {}
_CACHE_TIME   = 0.0
_CACHE_TTL    = 0.8      # 秒

_RE_LINE1 = re.compile(r"^\|\s*(\d+)\s+(\S+).*?\|\s*(\S+)\s+\|\s*([\d.]+)\s+(\d+)")
_RE_LINE2 = re.compile(r"^\|\s*\d+\s+\|\s+([0-9A-Fa-f:.]+).*?\|\s+(\d+)")
_RE_PROC  = re.compile(r"^\|\s*(\d+)\s+\d+\s+\|\s+(\d+)\s+\|.*?\|\s+(\d+)")

def _update_cache() -> None:
    global _CACHE_TIME
    if time.time() - _CACHE_TIME < _CACHE_TTL:
        return

    txt = subprocess.run(["npu-smi","info"], text=True,
                         capture_output=True, timeout=3).stdout.splitlines()

    data: dict[int, dict[str,Any]] = {}
    cur_npu = -1
    for ln in txt:
        m1 = _RE_LINE1.match(ln)
        if m1:                                    # 第一行
            npu, name, ok, pwr, tmp = m1.groups()
            cur_npu = int(npu)
            d = data.setdefault(cur_npu,{})
            d.update(name=name, health=ok,
                     power=float(pwr)*1000,       # W → mW
                     temp=int(tmp))
            continue

        m2 = _RE_LINE2.match(ln)
        if m2 and cur_npu >= 0:                   # 第二行
            bus, aic = m2.groups()
            pairs = re.findall(r'(\d+)\s*/\s*(\d+)', ln)
            h_used, h_total = map(int, pairs[-1]) if pairs else (0,0)
            d = data.setdefault(cur_npu,{})
            d.update(
                bus_id   = bus,
                aicore   = int(aic),
                hbm_used = h_used  * 1024 * 1024,
                hbm_total= h_total * 1024 * 1024,
            )
            continue

        mp = _RE_PROC.match(ln)
        if mp:
            npu_id, pid, mem = map(int, mp.groups())
            d = data.setdefault(npu_id,{})
            d.setdefault("procs",[]).append(
                (pid, mem*1024*1024)
            )

    # 補缺字段 + util 计算
    Util = namedtuple("UtilizationRates","npu mem bandwidth aicpu")
    for d in data.values():
        d.setdefault("power",NA); d.setdefault("temp",NA)
        d.setdefault("aicore",NA)
        d.setdefault("hbm_used",0); d.setdefault("hbm_total",0)
        d.setdefault("procs",[])
        mem_pct = (round(100*d["hbm_used"]/d["hbm_total"],1)
                   if d["hbm_total"] else NA)
        d["util"] = Util(d["aicore"], mem_pct, NA, NA)

    _CACHE.clear(); _CACHE.update(data)
    _CACHE_TIME = time.time()

# ----------------- 查询函数 -----------------
MemInfo  = namedtuple("MemoryInfo","total free used")
ProcInfo = namedtuple("Proc","pid usedNpuMemory")
def ascendDeviceGetCount():                _update_cache(); return len(_CACHE)
def ascendDeviceGetName(i):                _update_cache(); return _CACHE.get(i,{}).get("name",NA)
def ascendDeviceGetTemperature(i):         _update_cache(); return _CACHE.get(i,{}).get("temp",NA)
def ascendDeviceGetPowerUsage(i):          _update_cache(); return _CACHE.get(i,{}).get("power",NA)
def ascendDeviceGetUtilizationRates(i):    _update_cache(); return _CACHE.get(i,{}).get("util",NA)
def ascendDeviceGetMemoryInfo(i):
    _update_cache()
    d=_CACHE.get(i,{})
    tot=d.get("hbm_total",0); used=d.get("hbm_used",0)
    return MemInfo(tot, tot-used, used)
def ascendDeviceGetProcessInfo(i):
    _update_cache()
    return [ProcInfo(pid,mem) for pid,mem in _CACHE.get(i,{}).get("procs",[])]

# --------------- NVML 兼容小工具 ---------------
def nvmlCheckReturn(v:Any, t:type|tuple[type,...]|None=None)->bool:
    return v is not NA and (isinstance(v,t) if t else True)
def nvmlQuery(func:Callable|str,*a,default:Any=NA,**kw)->Any:
    try: f = globals()[func] if isinstance(func,str) else func; return f(*a,**kw)
    except Exception: return default
VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")

# ---- context manager magic ----
class _Mod(ModuleType):
    def __getattr__(self,n): return globals()[n]
    def __enter__(self): _ensure_acl(); return self
    def __exit__(self,*exc): ...
sys.modules[__name__].__class__ = _Mod