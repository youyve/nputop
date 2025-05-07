# nputop/api/libascend.py  –  v6  (2024-05-07)
# ===============================================================
# Ascend “伪 NVML” 兼容层 – 极速实时刷新 & 稳定进程列表
# ===============================================================
from __future__ import annotations
import atexit, os, re, shlex, subprocess, sys, threading, time
from collections import defaultdict, namedtuple
from types import ModuleType
from typing import Any, Callable, Dict, TypeAlias

# ────────────────────────── 基本常量 ────────────────────────────
NA: str = "N/A"
UINT_MAX = 0xFFFFFFFF
ULONGLONG_MAX = 0xFFFFFFFFFFFFFFFF
VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")
c_aclDevice_t: TypeAlias = int

# ────────────────────────── pyACL 初始化 ─────────────────────────
import acl  # noqa:  E402

_acl_lock = threading.Lock()
_acl_inited = False
def _acl_init() -> None:
    global _acl_inited
    with _acl_lock:
        if not _acl_inited:
            acl.init()
            _acl_inited = True
            atexit.register(lambda: acl.finalize())

# ────────────────────────── 通用执行 & LRU ───────────────────────
_LRU: Dict[str, tuple[float, str]] = {}
def _run_smi(section: str, card: int | None = None, ttl: float = 1.0) -> str:
    """小 TTL LRU 封装，避免反复 fork。"""
    key = f"{section}-{card}"
    now = time.time()
    ts, out = _LRU.get(key, (0.0, ""))
    if now - ts < ttl:
        return out
    cmd = f"npu-smi info -t {section}"
    if card is not None:
        cmd += f" -i {card}"
    try:
        out = subprocess.check_output(shlex.split(cmd), text=True)
    except Exception:
        out = ""
    _LRU[key] = (now, out)
    return out

# ────────────────────────── 设备 & SOC 名 ────────────────────────
_dev_cnt: int | None = None
_soc_name: str | None = None
def ascendDeviceGetCount() -> int:
    global _dev_cnt
    if _dev_cnt is None:
        _acl_init()
        ret = acl.rt.get_device_count()
        _dev_cnt = int(ret[0] if isinstance(ret, (list, tuple)) else ret)
    return _dev_cnt
def ascendDeviceGetName(_: int) -> str | None:
    global _soc_name
    if _soc_name is None:
        _acl_init()
        _soc_name = acl.get_soc_name()
    return _soc_name

# ────────────────────────── 显存信息 ────────────────────────────
Mem = namedtuple("MemoryInfo", "total free used")
def ascendDeviceGetMemoryInfo(dev: int) -> Mem:
    _acl_init()
    free, total, _ = acl.rt.get_mem_info(dev)
    if total == 0:                          # 罕见旧固件
        txt = _run_smi("usages", dev)
        cap = re.search(r"HBM Capacity\(MB\)\s*:\s*(\d+)", txt)
        total = int(cap.group(1)) * 2**20 if cap else 0
        used_pct = re.search(r"HBM Usage Rate\(%\)\s*:\s*(\d+)", txt)
        free = total * (100 - int(used_pct.group(1))) // 100 if used_pct else 0
    return Mem(total, free, total - free)

# ────────────────────────── 温度 / 功耗 ──────────────────────────
_TEMP_RE = re.compile(r"NPU Temperature \(C\)\s*:\s*(\d+)", re.I)
_POW_RE  = re.compile(r"Power\(W\)\s*:\s*([\d.]+)", re.I)
def ascendDeviceGetTemperature(dev: int):
    txt = _run_smi("temp", dev, ttl=1.0)
    return int(_TEMP_RE.search(txt).group(1)) if _TEMP_RE.search(txt) else NA
def ascendDeviceGetPowerUsage(dev: int):
    txt = _run_smi("power", dev, ttl=1.0)
    return int(float(_POW_RE.search(txt).group(1)) * 1000) if _POW_RE.search(txt) else NA

# ────────────────────────── 利用率 ───────────────────────────────
Util = namedtuple("UtilizationRates", "ai_core memory bandwidth aicpu")
_acl_ok: Dict[int, bool] = defaultdict(lambda: True)
def ascendDeviceGetUtilizationRates(dev: int):
    """成功一次则永久走 ACL；失败一次则永久走 npu-smi."""
    if _acl_ok[dev]:
        _acl_init()
        try:
            info, ret = acl.rt.get_device_utilization_rate(dev)
            if ret == 0:
                return Util(info.cube_utilization, info.memory_utilization, NA, info.aicpu_utilization)
        except Exception:
            _acl_ok[dev] = False
    # fallback：0.15 s TTL
    txt = _run_smi("usages", dev, ttl=0.15)
    ac = re.search(r"Aicore Usage Rate\(%\)\s*:\s*(\d+)", txt)
    mu = re.search(r"HBM Usage Rate\(%\)\s*:\s*(\d+)", txt)
    bw = re.search(r"HBM Bandwidth Usage Rate\(%\)\s*:\s*(\d+)", txt)
    return Util(int(ac.group(1)) if ac else NA,
                int(mu.group(1)) if mu else NA,
                int(bw.group(1)) if bw else NA,
                NA)

# ────────────────────────── 进程列表 ────────────────────────────
PROCESS_RE = re.compile(r"Process id:(\d+).*?Process memory\(MB\):(\d+)", re.I)
PROC_RE_OLD = re.compile(r"^\s*(\d+)\s+(\d+)", re.M)
Proc = namedtuple("Proc", "pid usedNpuMemory")
_last_proc: Dict[int, list[Proc]] = defaultdict(list)
def ascendDeviceGetProcessInfo(dev: int):
    # 0.25 s 缓存
    txt = _run_smi("proc-mem", dev, ttl=0.25)
    procs: list[Proc] = [
        Proc(int(pid), int(mem) * 2**20) for pid, mem in PROCESS_RE.findall(txt)
    ] or [
        Proc(int(pid), int(mem) * 2**20) for pid, mem in PROC_RE_OLD.findall(txt)
    ]
    if procs:
        _last_proc[dev] = procs
        return procs
    return _last_proc[dev]      # 无解析结果 → 上次快照，避免闪烁

# ────────────────────────── 驱动版本 ────────────────────────────
def ascendSystemGetDriverVersion() -> str | None:
    txt = _run_smi("board", ttl=30.0)
    m = re.search(r"Driver Version\s*:\s*(\S+)", txt)
    return m.group(1) if m else NA

# ────────────────────────── NVML 兼容层 API ─────────────────────
def nvmlCheckReturn(v: Any, types: type | tuple[type, ...] | None = None):  # noqa: ANN001
    return v != NA and (isinstance(v, types) if types else True)

def nvmlQuery(func: Callable[..., Any] | str, *a: Any, default: Any = NA, **kw: Any):  # noqa: ANN001
    try:
        _acl_init()
        if isinstance(func, str):
            func = globals()[func]
        return func(*a, **kw)
    except Exception:
        return default

# ────────────────────────── 模块包装 (with 支持) ────────────────
class _Mod(ModuleType):
    def __getattr__(self, n):       # noqa: ANN001
        if n in globals():
            return globals()[n]
        raise AttributeError(n)
    def __enter__(self):            # noqa: D401
        _acl_init(); return self
    def __exit__(self, *_):
        return False
sys.modules[__name__].__class__ = _Mod
