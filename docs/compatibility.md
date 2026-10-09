# Device compatibility

nputop supports Ascend 910B, 910C / Atlas A3, 310P, and 310B through the
`npu-smi` backend. Metrics depend on the model, driver, permissions and deployment;
support does not imply that every SKU or driver combination has been tested.

## Backends and topology

Automatic mode prefers DCMI and falls back to `npu-smi` after core collection
failures or timeouts. Explicit `--backend dcmi` or `--backend smi` selections
stay fixed. Press `r` to retry the requested backend.

Cards and chips are enumerated from the driver, including non-contiguous IDs.
nputop never assumes a fixed number of chips per card. Optional DCMI symbols
and capabilities are probed individually; an unsupported metric does not make
the whole device unavailable.

- **910C / A3:** preserves each compute die's temperature, memory and utilization.
  Same-card power is grouped for display; see [power sources](power.md) for its
  scope and estimated reference.
- **910B:** supports single-chip cards and explicit runtime ID mappings.
- **310P:** uses DDR telemetry when HBM is unsupported. Unsupported overall
  utilization falls back to AICore as `UTL*`, with its source in details and
  history. Unsupported chip power can use the optional MCU card-power interface.
  Missing power remains `NA`.
- **310B:** supported by the SMI parser; DCMI availability depends on the driver.

Real-device checks cover 910C, 910B3 and 310P3. The 910B3/310P3 checks cover idle
telemetry and mapping; loaded-process attribution and metric trends still need
validation. 310B is covered by recorded parser fixtures. Other SKUs, firmware
and driver combinations need their own checks; simulated tests cannot replace
hardware evidence.

## Metric meanings

| Field | Meaning |
| --- | --- |
| MEM | Used / total NPU memory; not bandwidth utilization |
| AICore | Compute-core utilization, DCMI selector 2 |
| UTL | Overall NPU utilization, DCMI selector 13; `UTL*` identifies the 310P fallback |
| MBW | Memory-bandwidth utilization, not GB/s |
| PWR | Current power relative to a reference, labeled `ref`; not a verified enforced limit |
| Clock | Driver-reported frequency in MHz |

See [metric sources and power interpretation](power.md) for query selection,
units and limitations. Ascend counters need not share NVIDIA NVML's sampling
definitions. Per-process NPU utilization, bandwidth and clock are unavailable
with the current backends; device values are never substituted.

Missing values remain unavailable; genuine zero is `0`. Retained stale samples
carry `*` and are excluded from valid aggregate history. The `UTL*` label
denotes a fallback source, independently of a sample's freshness.
SMI uses only fields in its output and does not silently call DCMI.

## Device numbering and filters

`--only` selects **display IDs**. `--only-visible` selects verified **runtime
logical IDs**, obtained from DCMI or the explicit `npu-smi info -m` table.
Neither card numbers nor filtered list positions stand in for logical IDs.

`ASCEND_RT_VISIBLE_DEVICES` takes priority; `CUDA_VISIBLE_DEVICES` remains
a compatibility fallback only when the Ascend variable is absent. An empty
value selects no devices. Values must be unique non-negative integers in
ascending order without spaces; a missing runtime ID ends the effective list.
`--only` takes precedence. An unavailable mapping returns an explicit error,
rather than showing all devices.

Three- and four-numeric-column SMI mapping tables are supported. Missing
physical IDs remain unknown, and MCU rows without logical IDs are skipped.
`ASCEND_VISIBLE_DEVICES` configures container deployment and is not treated as
runtime indices. See [Huawei's runtime-ID documentation](https://www.hiascend.com/document/detail/en/canncommercial/850/maintenref/envvar/envref_07_0028.html).

## Process identity and privacy

A process is identified by PID namespace, PID and creation time. Unverified
namespace rows stay read-only and are not resolved against possibly colliding
local PIDs. Signal confirmation rechecks identity, permission and freshness;
duplicate rows for one process receive one signal. Tree operations target only
explicit selections, not an entire subtree. `--readonly` blocks all signals.

Environment contents are read only on page entry or manual refresh and excluded
from normal snapshots and diagnostic exports. Host inspection uses a separate
bounded worker so it cannot block NPU collection.

`--diagnose` omits PIDs, usernames, commands, environment contents, hostname
and PCI addresses. `--json` is a full snapshot and **can contain process data**;
review it before sharing.

## Version and interface compatibility

Python 3.7+ and existing dependency minima are retained. Linux and an Ascend
driver are required for hardware monitoring. Imports, help and version work
without a driver; hardware collection reports an explicit error.

The native dashboard is the default in 0.1.0. `--legacy-ui` selects the previous
interface; `--preview` remains an alias. The default interval is two seconds.
Existing theme, threshold and filter options and `nputop_*` configuration
variables remain supported; command-line options take precedence.

Existing Python APIs and JSON fields keep their names and units. Optional
snapshot fields include logical/physical IDs and their source, power-reference
metadata, memory bandwidth (%), memory clock (MHz), HBM temperature (C), host
SWAP capacity/usage (bytes), and process RSS (bytes). The historical SMI
`physical_id` is a parser ID, not proof of a hardware physical ID.

NVIDIA compute/graphics process filters remain parseable for CLI compatibility
but return a clear unsupported-filter error in the native UI.

For device feedback, use the read-only acceptance tool in the
[contributor guide](development.md). It requires no root and uploads nothing.
Include the marketed model and driver version: a generic driver name such as
`Ascend910` may not identify the exact hardware.
