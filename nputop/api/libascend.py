"""
libascend.py – Ascend NPU 的“伪 NVML”兼容层
==========================================

* 提供与 nputop 原 libnvml.py 相同的关键接口：
    - nvmlQuery(...)
    - nvmlCheckReturn(...)
    - VERSIONED_PATTERN（保持占位）
* 依赖：
    - pyACL   (CANN 自带)
    - npu-smi (Ascend 驱动自带)
    - Python 标准库
* 设计：
    1. **pyACL 优先**：能直接拿到的数据走 ACL；
    2. **npu-smi 兜底**：ACL 没有的，通过命令行解析；
    3. 保证所有接口**不抛异常**，返回值类型稳定。
"""

from __future__ import annotations

import atexit
import re
import subprocess
import sys
import threading
from collections import namedtuple
from types import ModuleType
from typing import Any, Callable, TypeAlias

# ============================= 常量 =============================
NA: str = "N/A"
UINT_MAX: int = 0xFFFFFFFF
ULONGLONG_MAX: int = 0xFFFFFFFFFFFFFFFF

# 占位类型，用于 isinstance 判断
c_aclDevice_t: TypeAlias = int

# ============================= pyACL 初始化 =========================
import acl  # pyACL

__init_lock = threading.Lock()
__inited = False
__set_dev_once = False  # 必须先 set_device 才能用部分 ACL 接口


def ascendInit(dev_id: int | None = None) -> None:
    """线程安全 init；多次调用无害。"""
    global __inited, __set_dev_once
    with __init_lock:
        if not __inited:
            acl.init()
            __inited = True
            atexit.register(ascendShutdown)
        if (not __set_dev_once) and (dev_id is not None):
            acl.rt.set_device(dev_id)
            __set_dev_once = True


def ascendShutdown() -> None:
    """Finalize ACL，上一次 init 对应一次 finalize。"""
    global __inited
    with __init_lock:
        if __inited:
            acl.finalize()
            __inited = False


def _ensure_init(dev_id: int | None = None) -> None:
    ascendInit(dev_id)


# ============================= npu-smi 工具 ========================
def _run_smi(cmd: str) -> str:
    """执行 npu-smi 并返回 stdout，异常返回空串。"""
    try:
        return subprocess.run(
            cmd.split(), stdout=subprocess.PIPE, text=True, check=True
        ).stdout
    except Exception:
        return ""


# ============================= 设备通用 API ========================
def ascendDeviceGetCount() -> int:
    """
    返回 Ascend 设备数量，兼容 pyACL 可能返 (count, ret) 或单值 count。
    """
    _ensure_init()
    result = acl.rt.get_device_count()
    if isinstance(result, (tuple, list)) and result:
        count = result[0]
    else:
        count = result
    try:
        return int(count)
    except Exception:
        return 0


def ascendDeviceGetName(dev_id: int) -> str | None:
    """返回设备型号字符串，例如 "Ascend910B"。"""
    _ensure_init(dev_id)
    return acl.get_soc_name(dev_id)


# ---------- 显存 ----------
def ascendDeviceGetMemoryInfo(dev_id: int):
    """
    返回 namedtuple(total, free, used)，单位 Byte
    """
    _ensure_init(dev_id)
    free, total, _ = acl.rt.get_mem_info(dev_id)
    used = total - free
    Mem = namedtuple("MemoryInfo", "total free used")
    return Mem(total, free, used)


# ---------- 温度 ----------
_TEMP_RE = re.compile(r"NPU Temperature \(C\)\s*:\s*(\d+)", re.I)


def ascendDeviceGetTemperature(dev_id: int):
    """
    1) 尝试 `npu-smi info -t temp -i id`
    2) 若不支持，再降级到 watch
    """
    txt = _run_smi(f"npu-smi info -t temp -i {dev_id}")
    m = _TEMP_RE.search(txt)
    if m:
        return int(m.group(1))
    # fallback：watch 抓第一行
    txt = _run_smi(f"npu-smi info watch -i {dev_id} -s t -d 1")
    m = _TEMP_RE.search(txt)
    return int(m.group(1)) if m else NA


# ---------- 功耗 ----------
_POWER_RE = re.compile(r"Power\(W\)\s*:\s*(\d+)", re.I)


def ascendDeviceGetPowerUsage(dev_id: int):
    """返回 mW（与 NVML 单位对齐）。"""
    txt = _run_smi(f"npu-smi info -t power -i {dev_id}")
    m = _POWER_RE.search(txt)
    return int(m.group(1)) * 1000 if m else NA


# ---------- AI Core / Memory 利用率 ----------
_UTIL_RE = {
    "aicore": re.compile(r"Aicore Usage Rate\(%\)\s*:\s*(\d+)", re.I),
    "mem":    re.compile(r"HBM Usage Rate\(%\)\s*:\s*(\d+)",    re.I),
    "bw":     re.compile(r"HBM Bandwidth Usage Rate\(%\)\s*:\s*(\d+)", re.I),
    "aicpu":  re.compile(r"Aicpu Usage Rate\(%\)\s*:\s*(\d+)",  re.I),
}

Util = namedtuple("UtilizationRates", "ai_core mem bandwidth aicpu")


def ascendDeviceGetUtilizationRates(dev_id: int):
    try:
        _ensure_init(dev_id)
        info, ret = acl.rt.get_device_utilization_rate(dev_id)
        if ret == 0:
            # ACL 返回字段：cube_utilization, vector_utilization, aicpu_utilization, memory_utilization, utilization_extend
            return Util(
                info.cube_utilization,
                info.memory_utilization,
                NA,  # ACL 无带宽字段
                info.aicpu_utilization,
            )
    except Exception:
        pass

    # fallback: 解析 npu-smi info -t usages
    txt = _run_smi(f"npu-smi info -t usages -i {dev_id}")
    vals = {}
    for k, pat in _UTIL_RE.items():
        m = pat.search(txt)
        vals[k] = int(m.group(1)) if m else NA  
    return Util(vals["aicore"], vals["mem"], vals["bw"], vals["aicpu"])


# ---------- 进程显存列表 ----------
_PROC_RE = re.compile(r"^\s*(\d+)\s+(\d+)", re.M)


def ascendDeviceGetProcessInfo(dev_id: int):
    """
    返回 list of Proc(pid, usedNpuMemory)。
    尝试 -t proc-mem，不行再 -t proc。
    """
    txt = _run_smi(f"npu-smi info -t proc-mem -i {dev_id}")
    if not txt:
        txt = _run_smi(f"npu-smi info -t proc -i {dev_id}")
    procs = []
    for pid, mem_mb in _PROC_RE.findall(txt):
        proc = namedtuple("Proc", "pid usedNpuMemory")(int(pid), int(mem_mb) * 1024 * 1024)
        procs.append(proc)
    return procs


# ============================= 兼容层核心 =========================
def nvmlCheckReturn(value: Any, types: type | tuple[type, ...] | None = None) -> bool:
    if value == NA:
        return False
    return isinstance(value, types) if types else True


def nvmlQuery(
    func: Callable[..., Any] | str,
    *args: Any,
    default: Any = NA,
    ignore_errors: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Ascend 版万能查询器：
      - func 可为可调用对象或字符串名（本模块内）
      - 异常或返回 NA 时返 default（除非 ignore_errors=False）
    """
    try:
        if args and isinstance(args[0], int):
            _ensure_init(args[0])
        else:
            _ensure_init()
        if isinstance(func, str):
            func = globals()[func]
        return func(*args, **kwargs)
    except Exception:
        return default


# ---------------- 保持 VERSIONED_PATTERN 占位 ----------------
VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")

# ============================= 模块自包装 ====================
class _CustomModule(ModuleType):
    """支持 `with libascend:` 语法，及 getattr 兜底。"""

    def __getattr__(self, item: str):
        try:
            return globals()[item]
        except KeyError:
            raise AttributeError(item)

    def __enter__(self):
        ascendInit()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ascendShutdown()


sys.modules[__name__].__class__ = _CustomModule
