# SPDX-License-Identifier: Apache-2.0
"""Ascend-native telemetry, backend selection, and isolated sampling.

All driver access happens serially inside one disposable child process. Frames
are plain serializable values; drawing a frame can never call the driver.
"""

from __future__ import annotations

import copy
import errno
import math
import multiprocessing
from multiprocessing.connection import wait as wait_connections
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import psutil

from nputop.api import libascend, libdcmi
from nputop.api.cann import cann_version as cann_version, detect_cann_version
from nputop.api.power import power_reference

ERROR_STATES = {-8002: 'permission', -8006: 'timeout', -8013: 'unsupported', -8255: 'unsupported'}
_INITIAL_PID_NAMESPACE = 'pid:[4026531836]'
CONTAINER_PID_SCOPES = {'dcmi-container': 'container', 'host-procfs': 'host'}


def numeric(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def clean(value):
    if isinstance(value, str):
        return ''.join(c if c.isprintable() else ' ' for c in value).strip()
    return str(value)


def _pid_chain(status_path):
    for line in status_path.read_text().splitlines():
        if line.startswith('NSpid:'):
            pids = tuple(map(int, line.split(':', 1)[1].split()))
            return pids if pids and min(pids) > 0 else ()
    return ()


def host_pid_namespace():
    """Prove that driver host PIDs can be resolved in this procfs view.

    Linux reserves 0xEFFFFFFC for the initial PID namespace (PROC_PID_INIT_INO,
    now PID_NS_INIT_INO in include/uapi/linux/nsfs.h). Unknown kernels, non-Linux
    systems, nested namespaces, or alternate procfs mounts fail closed. A
    reported container PID alone is not proof of which container it belongs to.
    """
    try:
        if getattr(psutil, 'PROCFS_PATH', '/proc') != '/proc':
            return None
        token = os.readlink('/proc/self/ns/pid')
        if token != _INITIAL_PID_NAMESPACE:
            return None
        if _pid_chain(Path('/proc/self/status')) != (os.getpid(),):
            return None
        return token
    except (OSError, ValueError):
        return None


def _process_pid_view(host_namespace, driver_scope):
    """根据驱动返回的 PID 范围验证当前 /proc 视图。"""
    if driver_scope == 'changed':
        return None, 'driver-pid-scope-changed'
    if host_namespace is not None and driver_scope != 'container':
        return host_namespace, 'initial-procfs'
    if driver_scope is None:
        return None, 'driver-pid-scope-unknown'
    if getattr(psutil, 'PROCFS_PATH', '/proc') != '/proc':
        return None, 'procfs-unverified'
    try:
        namespace = os.readlink('/proc/self/ns/pid')
        pids = _pid_chain(Path('/proc/self/status'))
        if driver_scope == 'container' and pids == (os.getpid(),):
            return namespace, 'dcmi-container'
        if (
            driver_scope == 'host'
            and len(pids) > 1
            and pids[-1] == os.getpid()
            and os.readlink('/proc/1/ns/pid') == _INITIAL_PID_NAMESPACE
        ):
            return namespace, 'host-procfs'
    except PermissionError:
        return None, 'procfs-permission'
    except (OSError, ValueError):
        pass
    return None, 'host-procfs-unavailable' if driver_scope == 'host' else 'procfs-unverified'


def _process_sample_tick():
    try:
        return int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
    except (AttributeError, OSError, ValueError):
        return None


def _process_start_tick(proc):
    # stat 的 comm 字段可以包含空格和括号，starttime 是第 22 个字段。
    return int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])


def _container_local_pid(proc, pid, namespace, source):
    pids = _pid_chain(proc / 'status')
    if source == 'dcmi-container':
        return pid if pids == (pid,) else None
    if len(pids) > 1 and pids[0] == pid and os.readlink(str(proc / 'ns' / 'pid')) == namespace:
        return pids[-1]
    return None


def send_container_signal(target, sig):
    """重新验证进程后，向当前 PID 命名空间中的目标发送信号。"""
    pid, local_pid = target['pid'], target.get('local_pid')
    start_tick = target.get('start_tick')
    source = target.get('pid_source')
    scope = CONTAINER_PID_SCOPES.get(source)
    namespace, verified_source = _process_pid_view(None, scope)
    if (
        namespace is None
        or namespace != target.get('pid_namespace')
        or verified_source != source
        or type(pid) is not int
        or pid <= 0
        or type(local_pid) is not int
        or local_pid <= 0
        or start_tick is None
    ):
        raise RuntimeError('无法验证当前 PID 命名空间和目标进程。')

    fd = os.open('/proc/{}'.format(pid), os.O_RDONLY | os.O_DIRECTORY)
    try:
        # 通过打开的目录验证同一个进程，避免路径被复用时重新打开另一进程。
        proc = Path('/proc/self/fd', str(fd))
        if _container_local_pid(proc, pid, namespace, source) != local_pid:
            raise RuntimeError('目标进程的 PID 映射已变化。')
        if _process_start_tick(proc) != start_tick:
            raise RuntimeError('进程已退出或 PID 已复用。')
        pidfd_send_signal = getattr(signal, 'pidfd_send_signal', None)
        if pidfd_send_signal is not None:
            try:
                pidfd_send_signal(fd, sig)
                return
            except OSError as exc:
                if exc.errno != errno.ENOSYS:
                    raise
                if _process_start_tick(proc) != start_tick:
                    raise RuntimeError('进程已退出或 PID 已复用。') from exc
        # 旧内核只能按数字 PID 发送。上面的检查紧邻发送，也保留内核的权限检查。
        os.kill(local_pid, sig)
    except (ValueError, IndexError) as exc:
        raise RuntimeError('无法读取目标进程的身份。') from exc
    finally:
        os.close(fd)


def _unknown_process(state, reason):
    messages = {
        'driver-pid-scope-unknown': '驱动 PID 范围未知',
        'driver-pid-scope-changed': '驱动 PID 范围在采样期间变更',
        'duplicate-driver-pid': '此设备报告了重复的 PID',
        'host-procfs-unavailable': '无法读取初始 PID 命名空间的 procfs',
        'procfs-unverified': '无法验证 procfs 的 PID 命名空间',
        'procfs-permission': '没有权限读取 procfs',
        'unverified-npu-memory': 'DCMI 未确认本地进程的显存占用',
        'target-namespace-mismatch': '进程不在已验证的 PID 命名空间内',
        'permission': '没有权限读取进程信息',
        'process-exited-or-hidden': '进程已退出或在当前环境不可见',
        'identity-changed': '进程在采样期间启动或 PID 被复用',
        'procfs-identity-unavailable': '无法从 procfs 确认进程身份',
    }
    return {
        'command': '[' + messages[reason] + ']',
        'host_state': state,
        'host_reason': reason,
    }


def known_virtual_machine():
    """Best-effort positive VM detection; a container alone is not a VM.

    A3 HBM bandwidth may be a meaningless zero in passthrough VMs. Do not
    describe absence of these host hints as proof of bare-metal deployment.
    """
    for name in ('/sys/hypervisor/type', '/sys/class/dmi/id/product_name', '/proc/cpuinfo'):
        try:
            value = Path(name).read_text().lower()
        except OSError:
            continue
        if name.endswith('/type') and value.strip():
            return True
        if any(
            word in value
            for word in ('hypervisor', 'qemu', 'kvm', 'vmware', 'virtualbox', 'virtual machine')
        ):
            return True
    return False


class ObservedDcmi(libdcmi.DcmiBackend):
    """Keep vendor return codes and avoid repeatedly calling unsupported APIs."""

    def __init__(self, library):
        self.calls = []
        self.capabilities = {}
        self._negative = {}
        self.hbm_cache = {}
        super().__init__(library)

    def hbm_info(self, index):
        # Per-sample reuse: capacity, bandwidth, clock and selected-device
        # details must not repeat the same hardware query in a single frame.
        if index in self.hbm_cache:
            value, calls = self.hbm_cache[index]
            self.calls.extend(calls)
            return value
        start = len(self.calls)
        value = super().hbm_info(index)
        self.hbm_cache[index] = (value, self.calls[start:])
        return value

    def _call(self, name, *args):
        # Pointer values are deliberately excluded from the capability key.
        scalar = tuple(arg for arg in args if isinstance(arg, int))
        key = (name, scalar)
        cached = self._negative.get(key)
        start = time.monotonic()
        if cached and start < cached[1]:
            code = cached[0]
        else:
            code = super()._call(name, *args)
            if code in (-8255, -8013) or (code is None and name not in self._functions):
                self._negative[key] = (code, start + 60)
        record = {
            'function': name,
            'arguments': scalar,
            'code': code,
            'state': 'ok' if code == 0 else ERROR_STATES.get(code, 'error'),
            'duration_ms': round((time.monotonic() - start) * 1000, 3),
        }
        if code is None and name not in self._functions:
            record['state'] = 'unsupported'
        self.calls.append(record)
        self.capabilities[key] = record
        return code

    def model(self, index):
        info = libdcmi._ChipInfoV2()
        if self._struct('dcmi_get_device_chip_info_v2', index, info) is not None:
            chip = libdcmi._decode_char_array(info.chip_name)
            npu = libdcmi._decode_char_array(info.npu_name)
            # Some A3 drivers return a numeric product code, e.g. 9382.
            # Preserve it in diagnostics but prefer a descriptive chip name.
            return (npu if npu and not npu.isdigit() else chip or npu), npu
        return self.name(index), None


class MetricStore:
    def __init__(self):
        self.good = {}

    def metric(self, key, value, state='unavailable', reason=None):
        now = time.monotonic()
        if numeric(value):
            self.good[key] = (value, now)
            return {'value': value, 'state': 'ok', 'at': now, 'reason': None}
        previous = self.good.get(key)
        if previous and now - previous[1] <= 10 and state not in ('unsupported', 'permission'):
            return {
                'value': previous[0],
                'state': 'stale',
                'at': previous[1],
                'reason': reason,
                'error_state': state,
            }
        return {'value': None, 'state': state, 'at': now, 'reason': reason}


class DcmiAdapter(MetricStore):
    name = 'dcmi'

    def __init__(self, library=None):
        super().__init__()
        self.dcmi = ObservedDcmi(library if library is not None else libdcmi.load_library())
        self.driver = self.dcmi.driver_version()
        self.inventory = []
        for i, ref in enumerate(self.dcmi._devices):
            model, product = self.dcmi.model(i)
            board_id = self.dcmi.board_id(i)
            reference = power_reference(
                model, libascend._POWER_LIMIT, board_id, self.dcmi.rated_power_info(i)
            )
            self.inventory.append(
                {
                    'id': i,
                    'key': f'{ref.card_id}:{ref.chip_id}',
                    'card': ref.card_id,
                    'chip': ref.chip_id,
                    'model': clean(model),
                    'product_code': product,
                    'board_id': board_id,
                    'power_reference': reference,
                    # A hypothesis about the rated reference does not prove
                    # the scope of the separate current-power interface.
                    'power_scope': 'driver-reported; scope unverified',
                    'bus': self.dcmi.bus_id(i),
                    **self.dcmi.device_ids(i),
                }
            )
        self._detail_cache = {}
        self._virtual_machine = known_virtual_machine()

    def query(self, index, names, function, convert=None):
        self.dcmi.calls.clear()
        value = function()
        values = convert(value) if convert else [value]
        failed = [call for call in self.dcmi.calls if call['state'] != 'ok']
        error = (
            failed[-1] if failed else {'state': 'unavailable', 'function': 'query', 'code': None}
        )
        if not self.dcmi.calls and all(v is None or not numeric(v) for v in values):
            error = {'state': 'unsupported', 'function': 'missing API', 'code': None}
        reason = f"{error['function']}: {error['code']}"
        return {
            name: self.metric((index, name), v, error['state'], reason)
            for name, v in zip(names, values)
        }

    def sample(self, detail_key=None):
        devices, processes, errors = [], [], []
        card_power = {}
        self.dcmi.hbm_cache.clear()
        scope = self.dcmi.process_pid_scope()
        for metadata in self.inventory:
            row = dict(metadata)
            i = row['id']
            metrics = self.query(
                i,
                ['memory_used', 'memory_total'],
                lambda: self.dcmi.memory_info(i),
                lambda v: [v.used, v.total],
            )
            metrics.update(self.memory_telemetry(i))
            metrics.update(self.query(i, ['aicore'], lambda: self.dcmi._utilization(i, 2)))
            metrics.update(self.query(i, ['npu_overall'], lambda: self.dcmi._utilization(i, 13)))
            metrics.update(
                self.query(
                    i,
                    ['aicore_mhz'],
                    lambda: self.dcmi._frequency(i, libdcmi.DCMI_FREQ_AICORE_CURRENT),
                )
            )
            metrics.update(self.query(i, ['temperature'], lambda: self.dcmi.temperature(i)))
            metrics.update(
                self.query(
                    i,
                    ['power'],
                    lambda: self.dcmi.power_usage(i),
                    lambda v: [v / 1000 if numeric(v) else None],
                )
            )
            row['power_source'] = 'dcmi_get_device_power_info'
            if '310P' in row['model'].upper() and metrics['power']['state'] == 'unsupported':
                # MCU telemetry belongs to the card, not an independently metered die.
                if row['card'] not in card_power:
                    card_power[row['card']] = self.query(
                        i,
                        ['mcu_power'],
                        lambda: self.dcmi.mcu_power_usage(i),
                        lambda v: [v / 1000 if numeric(v) else None],
                    )['mcu_power']
                metrics['power'] = dict(card_power[row['card']])
                row['power_source'] = 'dcmi_mcu_get_power_info'
                if numeric(metrics['power']['value']):
                    row['power_scope'] = 'card-shared'
            row['metrics'] = metrics
            # Raw current power is preserved even when card-shared. Never sum
            # die rows or manufacture independent per-die limits by dividing.
            try:
                entries = self.dcmi.process_info(i, strict=True)
                row['process_state'] = 'ok'
                processes.extend(
                    {'device': row['key'], 'device_id': i, 'pid': p.pid, 'memory': p.usedNpuMemory}
                    for p in entries
                )
            except libdcmi.DcmiQueryError as exc:
                row['process_state'] = (
                    ERROR_STATES.get(exc.code, 'error') if exc.code is not None else 'unsupported'
                )
                errors.append(f"{row['key']} processes: {row['process_state']} ({exc.code})")
            row['details'] = {}
            if detail_key in (row['key'], '*'):
                row['details'] = self.details(i)
                for name in (
                    'memory_bandwidth',
                    'memory_mhz',
                    'hbm_temperature',
                    'aicore_mhz',
                    'npu_overall',
                ):
                    row['details'][name] = copy.deepcopy(metrics[name])
            devices.append(row)
        if processes and scope is not None and self.dcmi.process_pid_scope() != scope:
            scope = 'changed'
        return {
            'devices': devices,
            'processes': processes,
            '_process_pid_scope': scope,
            'errors': errors,
            'process_status': 'ok' if not errors else ('partial' if processes else 'unavailable'),
        }

    def memory_telemetry(self, index):
        result = self.query(
            index,
            ['memory_bandwidth', 'memory_mhz', 'hbm_temperature'],
            lambda: self.dcmi.hbm_info(index),
            lambda v: [
                v.bandwidth,
                v.frequency if numeric(v.frequency) and 0 < v.frequency < 1000000 else None,
                v.temperature,
            ],
        )
        hbm_calls = self.dcmi.hbm_cache[index][1]
        hbm_source = hbm_calls[-1]['function'] if hbm_calls else 'HBM information'
        for name in result:
            result[name]['source'] = hbm_source
        # DDR devices (including 310P) use selector 5 / frequency 1. Only an
        # unsupported capability permits switching paths, not a transient error
        # or a permission failure. Retained stale values keep their source.
        for name, hbm_selector, ddr_selector, method in (
            ('memory_bandwidth', 10, 5, self.dcmi._utilization),
            ('memory_mhz', libdcmi.DCMI_FREQ_HBM, libdcmi.DCMI_FREQ_DDR, self.dcmi._frequency),
        ):
            if result[name]['state'] in ('unsupported', 'unavailable'):
                convert = (
                    (lambda v: [v if numeric(v) and 0 < v < 1000000 else None])
                    if name == 'memory_mhz'
                    else None
                )
                result.update(
                    self.query(index, [name], lambda: method(index, hbm_selector), convert)
                )
                selector = hbm_selector
                if result[name]['state'] == 'unsupported':
                    result.update(
                        self.query(index, [name], lambda: method(index, ddr_selector), convert)
                    )
                    selector = ddr_selector
                result[name]['source'] = '{}({})'.format(
                    'dcmi_get_device_utilization_rate'
                    if name == 'memory_bandwidth'
                    else 'dcmi_get_device_frequency',
                    selector,
                )
        bandwidth = result['memory_bandwidth']
        if (
            self._virtual_machine
            and bandwidth['value'] == 0
            and not bandwidth['source'].endswith('(5)')
        ):
            self.good.pop((index, 'memory_bandwidth'), None)
            bandwidth.update(
                value=None,
                raw_value=0,
                state='unavailable',
                reason='HBM bandwidth zero is not reliable in a passthrough VM',
            )
        return result

    def details(self, index):
        cached = self._detail_cache.get(index)
        if cached and time.monotonic() - cached[0] < 5:
            return copy.deepcopy(cached[1])
        result = self.query(index, ['aicpu'], lambda: self.dcmi._utilization(index, 3))
        result.update(
            self.query(
                index,
                ['ecc_uncorrected'],
                lambda: self.dcmi.total_volatile_uncorrected_ecc_errors(index),
            )
        )
        result.update(
            self.query(
                index,
                ['pcie_tx', 'pcie_rx'],
                lambda: self.dcmi.pcie_throughput(index),
                lambda v: [
                    v.tx / 1024**2 if numeric(v.tx) else None,
                    v.rx / 1024**2 if numeric(v.rx) else None,
                ],
            )
        )
        self._detail_cache[index] = (time.monotonic(), result)
        return copy.deepcopy(result)

    def diagnostics(self):
        return sorted(
            self.dcmi.capabilities.values(), key=lambda v: (v['function'], v['arguments'])
        )


class SmiAdapter(MetricStore):
    name = 'smi'

    def __init__(self):
        super().__init__()
        self.driver = None
        self.mapping = None

    def sample(self, detail_key=None):
        timeout = libascend._smi_timeout()
        if detail_key == '@mapping' and self.mapping is None:
            from nputop.api.visibility import parse_mapping

            try:
                mapping = subprocess.run(
                    ['npu-smi', 'info', '-m'],
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )
                self.mapping = parse_mapping(mapping.stdout)
            except (OSError, subprocess.SubprocessError):
                self.mapping = {}
        result = subprocess.run(
            ['npu-smi', 'info'], text=True, capture_output=True, timeout=timeout, check=True
        )
        raw = result.stdout
        if 'npu-smi' not in raw or 'Version:' not in raw:
            raise RuntimeError('npu-smi returned an unrecognized document')
        libascend._update_cache(raw)
        self.driver = libascend._DRIVER_VERSION
        devices, processes = [], []
        process_table = 'Process id' in raw
        overall = 'NPU Util(%)' in raw
        utilization = 'npu_overall' if overall else 'aicore'
        for i, physical in enumerate(libascend._IDX):
            data = libascend._CACHE[physical]
            key = f"{data['npu_id']}:{data['chip_id']}"
            row = {
                'id': i,
                'key': key,
                'card': data['npu_id'],
                'chip': data['chip_id'],
                'physical_id': physical,  # Existing parser ID retained for JSON compatibility.
                **(
                    (self.mapping or {}).get(
                        key, dict(logical_id=None, chip_physical_id=None, id_source=None)
                    )
                ),
                'model': data['name'],
                'bus': data.get('bus_id'),
                'power_scope': 'driver-reported; scope unverified',
                'details': {},
                'process_state': 'ok' if process_table else 'unavailable',
            }
            row['power_reference'] = power_reference(data['name'], libascend._POWER_LIMIT)
            values = {
                'memory_used': data.get('hbm_used'),
                'memory_total': data.get('hbm_total'),
                utilization: data.get('aicore'),
                'temperature': data.get('temp'),
                'power': data['power'] / 1000 if numeric(data.get('power')) else None,
            }
            row['metrics'] = {
                name: self.metric((key, name), value) for name, value in values.items()
            }
            for name in ('memory_bandwidth', 'memory_mhz'):
                row['metrics'][name] = self.metric(
                    (key, name), None, 'unsupported', 'Not reported by npu-smi info'
                )
            row['primary_utilization'] = utilization
            if overall:
                row['metrics']['aicore'] = self.metric(
                    (key, 'aicore'), None, 'unsupported', 'SMI reports overall NPU, not AICore'
                )
                row['details']['npu_overall'] = row['metrics']['npu_overall']
            row['health'] = data.get('health')
            devices.append(row)
            processes.extend(
                {
                    'device': key,
                    'device_id': i,
                    'pid': pid,
                    'memory': memory,
                    'container_pid': libascend._PROC_CONTAINER_PIDS.get(
                        (data['npu_id'], data['chip_id'], pid)
                    ),
                }
                for pid, memory in data['procs']
            )
        process_text = raw.split('Process id', 1)[-1] if process_table else ''
        process_rows = sum(
            bool(re.match(r'^\s*\|\s*\d+', line)) for line in process_text.splitlines()
        )
        complete = (
            process_table
            and process_rows == len(processes)
            and (process_rows > 0 or 'No running processes' in process_text)
        )
        if not complete:
            for row in devices:
                row['process_state'] = 'unavailable'
        return {
            'devices': devices,
            'processes': processes,
            'process_status': 'ok' if complete else ('partial' if processes else 'unavailable'),
            'errors': [] if complete else ['npu-smi process table missing or incomplete'],
        }

    def diagnostics(self):
        return [{'function': 'npu-smi info', 'timeout_seconds': libascend._SMI_TIMEOUT}]


def healthy(frame):
    if not frame['devices']:
        return False
    return all(
        all(
            row['metrics'][key]['state'] == 'ok'
            for key in ('memory_used', 'memory_total', row.get('primary_utilization', 'aicore'))
        )
        and row['metrics']['memory_total']['value'] > 0
        for row in frame['devices']
    )


class BackendManager:
    def __init__(self, mode='auto', dcmi_factory=DcmiAdapter, smi_factory=SmiAdapter):
        self.mode = mode
        self.factories = {'dcmi': dcmi_factory, 'smi': smi_factory}
        self.adapter = None
        self.reason = None
        self.failures = 0
        # A monotonic clock has an unspecified origin (process-relative on
        # older macOS Python). No previous attempt must mean no cooldown.
        self.last_fallback = None
        self.cann, self.cann_detection = detect_cann_version()
        self.process_cache = {}
        psutil.cpu_percent()

    def _create(self, name, detail):
        adapter = self.factories[name]()
        return adapter, self._sample(adapter, detail)

    def _sample(self, adapter, detail):
        sample_tick = _process_sample_tick()
        frame = adapter.sample(detail)
        frame['_process_sample_tick'] = sample_tick
        return frame

    def collect(self, detail=None):
        start = time.monotonic()
        if self.adapter is None:
            primary = 'dcmi' if self.mode == 'auto' else self.mode
            candidate = None
            try:
                candidate, frame = self._create(primary, detail)
                if self.mode == 'auto' and (not healthy(frame) or frame['process_status'] != 'ok'):
                    raise RuntimeError('DCMI core telemetry or process capability incomplete')
                self.adapter = candidate
            except Exception as exc:
                if self.mode != 'auto':
                    raise
                self.reason = f'DCMI unavailable: {type(exc).__name__}: {exc}'
                try:
                    alternative, alternative_frame = self._create('smi', detail)
                    if not healthy(alternative_frame):
                        raise RuntimeError('SMI core telemetry incomplete or no visible devices')
                    self.adapter, frame = alternative, alternative_frame
                except Exception as fallback_exc:
                    if candidate is None:
                        raise RuntimeError(f'{self.reason}; SMI: {fallback_exc}') from fallback_exc
                    self.adapter = candidate
                    self.reason += f'; SMI unavailable: {fallback_exc}'
        else:
            try:
                frame = self._sample(self.adapter, detail)
            except Exception:
                self.failures += 1
                if (
                    self.mode == 'auto'
                    and self.adapter.name == 'dcmi'
                    and self.failures >= 3
                    and self._can_fallback()
                ):
                    return self._fallback(detail, start)
                raise
        if healthy(frame) and frame['process_status'] == 'ok':
            self.failures = 0
        else:
            self.failures += 1
        if self.mode == 'auto' and self.adapter.name == 'dcmi' and self.failures >= 3:
            # Retry failed fallback at most once a minute, not every refresh.
            if self._can_fallback():
                return self._fallback(detail, start, frame)
        return self._finish(frame, start)

    def _can_fallback(self):
        return self.last_fallback is None or time.monotonic() - self.last_fallback > 60

    def _fallback(self, detail, start, previous=None):
        self.last_fallback = time.monotonic()
        try:
            adapter, frame = self._create('smi', detail)
            if not healthy(frame):
                raise RuntimeError('SMI core telemetry incomplete')
        except Exception as exc:
            if previous is None:
                raise
            self.reason = f'DCMI degraded; SMI fallback failed: {exc}'
            return self._finish(previous, start)
        self.adapter, self.failures = adapter, 0
        self.reason = 'Switched to SMI after 3 consecutive core/process failures; r retries DCMI'
        return self._finish(frame, start)

    def _finish(self, frame, start):
        self._enrich_processes(
            frame['processes'],
            frame.pop('_process_pid_scope', None),
            frame.pop('_process_sample_tick', None),
        )
        vm, swap = psutil.virtual_memory(), psutil.swap_memory()
        frame.update(
            backend=self.adapter.name,
            requested_backend=self.mode,
            reason=self.reason,
            driver=self.adapter.driver,
            cann=self.cann,
            cann_detection=self.cann_detection,
            sampled_at=time.monotonic(),
            collection_ms=round((time.monotonic() - start) * 1000, 2),
            host={
                'cpu': psutil.cpu_percent(),
                'memory_used': vm.used,
                'memory_total': vm.total,
                'memory_percent': vm.percent,
                'swap_percent': swap.percent,
                'swap_used': swap.used,
                'swap_total': swap.total,
                'load': list(os.getloadavg()) if hasattr(os, 'getloadavg') else [],
            },
            capabilities=self.adapter.diagnostics(),
        )
        return frame

    def _enrich_processes(self, rows, driver_scope=None, sample_tick=None):
        host_namespace = host_pid_namespace()
        namespace, source = _process_pid_view(host_namespace, driver_scope) if rows else (None, None)
        duplicates = Counter((row.get('device'), row['pid']) for row in rows)
        alive = set()
        details = {}
        for row in rows:
            pid = row['pid']
            row.update(
                driver_pid=pid,
                local_pid=None,
                pid_namespace=None,
                pid_source=None,
                signal_allowed=False,
                start_tick=None,
                user='?',
                created=None,
                cpu=None,
                host_memory=None,
                host_rss=None,
                time=None,
            )
            if namespace is None:
                row.update(_unknown_process('namespace-unverified', source))
                continue
            if source == 'dcmi-container':
                if duplicates[row.get('device'), pid] > 1:
                    row.update(_unknown_process('namespace-unverified', 'duplicate-driver-pid'))
                    continue
                # 普通容器模式按调用者 PID 命名空间解析 PID，再查询该进程的显存。
                # 只有正值才能确认本地同号进程确实使用了这张卡。
                if (row.get('memory') or 0) <= 0:
                    row.update(_unknown_process('namespace-unverified', 'unverified-npu-memory'))
                    continue
            key = (namespace, source, pid)
            if key not in details:
                details[key] = self._read_process(pid, namespace, source, sample_tick)
            row.update(details[key])
            if row['host_state'] == 'ok':
                alive.add(key)
                row['signal_allowed'] = host_namespace == namespace or (
                    source in CONTAINER_PID_SCOPES and row['start_tick'] is not None
                )
        self.process_cache = {key: self.process_cache[key] for key in alive}

    def _read_process(self, pid, namespace, source, sample_tick):
        proc = Path('/proc', str(pid))
        key = (namespace, source, pid)
        try:
            start_tick = None
            local_pid = pid
            if sample_tick is not None:
                start_tick = _process_start_tick(proc)
                # 同一个时钟滴答内无法可靠确定进程启动与驱动采样的先后。
                if start_tick >= sample_tick:
                    return _unknown_process('unavailable', 'identity-changed')
            if source != 'initial-procfs':
                if start_tick is None:
                    return _unknown_process('unavailable', 'procfs-identity-unavailable')
                local_pid = _container_local_pid(proc, pid, namespace, source)
                if local_pid is None:
                    return _unknown_process('namespace-unverified', 'target-namespace-mismatch')
            process = self.process_cache.get(key)
            if process is None or not process.is_running():
                process = psutil.Process(pid)
            with process.oneshot():
                created = process.create_time()
                result = {
                    'local_pid': local_pid,
                    'pid_namespace': namespace,
                    'pid_source': source,
                    'user': process.username(),
                    'cpu': process.cpu_percent(),
                    'host_memory': process.memory_percent(),
                    'host_rss': process.memory_info().rss,
                    'created': created,
                    'start_tick': start_tick,
                    'time': max(0, time.time() - created),
                    'command': clean(' '.join(process.cmdline()) or process.name()),
                    'host_state': 'ok',
                    'host_reason': None,
                }
            if not process.is_running() or (
                start_tick is not None and _process_start_tick(proc) != start_tick
            ):
                return _unknown_process('unavailable', 'identity-changed')
            self.process_cache[key] = process
            return result
        except (psutil.AccessDenied, PermissionError):
            return _unknown_process('permission', 'permission')
        except (psutil.NoSuchProcess, FileNotFoundError, ProcessLookupError):
            return _unknown_process('unavailable', 'process-exited-or-hidden')
        except (OSError, ValueError, IndexError):
            return _unknown_process('unavailable', 'procfs-identity-unavailable')


def _session_worker(connection, mode, ready, worker):
    if hasattr(os, 'setsid'):
        os.setsid()
    # Publish ownership before any driver call or descendant can be started.
    ready.set()
    worker(connection, mode)


def _worker(connection, mode):
    manager = BackendManager(mode)
    try:
        while True:
            command, detail = connection.recv()
            if command == 'stop':
                return
            try:
                connection.send(('frame', manager.collect(detail)))
            except Exception as exc:
                connection.send(('error', f'{type(exc).__name__}: {exc}'))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class Sampler:
    """Nonblocking parent-side sampler with a hard deadline and bounded restarts."""

    def __init__(self, mode='auto', interval=1.0, timeout=15.0, context=None):
        self.mode, self.interval, self.timeout = mode, interval, timeout
        self.context = context or multiprocessing.get_context('spawn')
        self.process = self.connection = None
        self.session_ready = None
        self.inflight = None
        self.next_sample = 0.0
        self.frame = None
        self.error = None
        self.fallback_reason = None
        self.worker_mode = mode
        self.failures = 0
        self.sequence = 0

    def _start(self):
        parent, child = self.context.Pipe()
        self.connection = parent
        self.session_ready = self.context.Event()
        self.process = self.context.Process(
            target=_session_worker,
            args=(child, self.worker_mode, self.session_ready, _worker),
            daemon=True,
        )
        self.process.start()
        child.close()

    def _exited(self):
        # Waiting on the sentinel observes exit without reaping the child. Its
        # PID/PGID cannot be reused before _stop has cleaned the owned session.
        return self.process is not None and bool(wait_connections([self.process.sentinel], 0))

    def _signal_group(self, group, sig):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin reports EPERM for a group containing only a dead leader.
            # Ignore it only after verifying that no live member remains.
            if sys.platform != 'darwin' or not self._exited():
                raise
            for process in psutil.process_iter():
                try:
                    if (
                        os.getpgid(process.pid) == group
                        and process.status() != psutil.STATUS_ZOMBIE
                    ):
                        raise RuntimeError('cannot stop a live member of the sampler group')
                except (ProcessLookupError, psutil.NoSuchProcess):
                    pass

    def _stop(self):
        if self.process is not None:
            group = None
            try:
                if hasattr(os, 'killpg'):
                    if self.session_ready is not None and self.session_ready.is_set():
                        group = self.process.pid
                    elif os.getpgid(self.process.pid) == self.process.pid:
                        group = self.process.pid
            except ProcessLookupError:
                pass
            if group is not None:
                # Do not join/is_alive (both can reap) until all group signals
                # have been sent, including when the worker has already died.
                self._signal_group(group, signal.SIGTERM)
                wait_connections([self.process.sentinel], 0.2)
                self._signal_group(group, signal.SIGKILL)
            elif not self._exited():
                # Before setsid completes there cannot yet be a driver child.
                self.process.terminate()
                if not wait_connections([self.process.sentinel], 0.2):
                    self.process.kill()
            self.process.join(0.2)
            if self.process.is_alive():
                raise RuntimeError(
                    'sampler could not be stopped; refusing to start another driver worker'
                )
            self.process.close()
        if self.connection is not None:
            self.connection.close()
        self.connection = self.process = None
        self.session_ready = None
        self.inflight = None

    def poll(self, detail=None):
        now = time.monotonic()
        if self.connection is not None and self.connection.poll():
            try:
                kind, payload = self.connection.recv()
            except (EOFError, OSError):
                kind, payload = 'crash', 'sampling worker exited unexpectedly'
            self.inflight = None
            if kind == 'frame':
                self.frame, self.error = payload, None
                if self.mode == 'auto' and payload['backend'] == 'smi':
                    self.worker_mode = 'smi'
                if self.fallback_reason:
                    self.frame['reason'] = self.fallback_reason
                    self.frame['requested_backend'] = self.mode
                self.failures = 0
                self.sequence += 1
                self.next_sample = now + self.interval
            elif kind == 'crash':
                self._failed_worker(payload, now)
            else:
                self.error = payload
                self.failures += 1
                self.next_sample = now + min(30, 2 ** min(self.failures, 5))
        if self._exited():
            self._failed_worker('sampling worker exited unexpectedly', now)
        if self.inflight is not None and now - self.inflight >= self.timeout:
            self._failed_worker(f'sampling timed out after {self.timeout:g}s', now)
        if self.inflight is None and now >= self.next_sample:
            if self.process is None:
                self._start()
            try:
                self.connection.send(('collect', detail))
                self.inflight = now
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._failed_worker(f'sampling worker connection failed: {exc}', now)
        return self.frame

    def _failed_worker(self, reason, now):
        self._stop()
        self.error = reason
        self.failures += 1
        if self.mode == 'auto' and self.worker_mode != 'smi':
            self.worker_mode = 'smi'
            self.fallback_reason = f'DCMI worker unavailable ({reason}); using SMI'
            self.next_sample = now
        else:
            self.next_sample = now + min(30, 2 ** min(self.failures, 5))

    def refresh(self):
        self._stop()
        self.worker_mode = self.mode
        self.fallback_reason = None
        self.next_sample = 0.0
        self.failures = 0
        self.error = None

    def close(self):
        self._stop()


def diagnostic_report(frame, error=None):
    """No PIDs, users, command lines, hostname, PCI addresses or process memory."""
    if frame is None:
        return {
            'schema': 1,
            'error': 'No snapshot available; run --once locally for the full error.',
        }
    identities = Counter(
        row.get('host_reason') or row.get('pid_source') or 'unknown'
        for row in frame.get('processes', ())
    )
    return {
        'schema': 1,
        'backend': frame['backend'],
        'requested_backend': frame['requested_backend'],
        'driver': frame['driver'],
        'cann': frame['cann'],
        'cann_detection': frame.get('cann_detection'),
        'process_identity': dict(identities),
        'collection_ms': frame['collection_ms'],
        'fallback': bool(frame.get('reason')),
        'error': bool(error),
        'devices': [
            {
                'id': row['id'],
                'card': row['card'],
                'chip': row['chip'],
                'model': row['model'],
                'product_code': row.get('product_code'),
                'board_id': row.get('board_id'),
                'power_scope': row.get('power_scope'),
                'power_source': row.get('power_source'),
                'power_reference': copy.deepcopy(row.get('power_reference')),
                'primary_utilization': row.get('primary_utilization', 'aicore'),
                'metrics': {
                    name: {'state': metric['state'], 'reason': metric['reason']}
                    for name, metric in {**row['metrics'], **row['details']}.items()
                },
                'process_state': row['process_state'],
            }
            for row in frame['devices']
        ],
        'capabilities': frame['capabilities'],
        'environment': {
            'uid_class': 'root' if hasattr(os, 'geteuid') and os.geteuid() == 0 else 'non-root',
            'container_hint': Path('/.dockerenv').exists(),
        },
    }
