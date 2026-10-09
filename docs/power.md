# Power references and metric sources

[中文说明](power_zh.md) · [README](../README.md)

## Read the power display

`Pwr:Usage/Ref` shows **current power / estimated rated reference**, in watts.
The main table rounds to whole watts; details and JSON retain original precision.
nputop does not currently report a verified enforced power limit.

For multi-chip cards, the first visible chip shows one pair, such as
`170W / 949W`; other rows show `↳ Card 0`. Each chip keeps its own temperature,
memory and utilization. Current power is the newest valid reading from the
card's enumerated chips. If all readings are stale, the newest retained value
is shown with `*`; if none exists, it is `NA`. Timestamp ties use the lowest
chip ID.

This is a representative interface reading. nputop does not sum, average or
divide readings whose measurement scopes may overlap, or infer whole-server
power. Filtering the view does not change the source. Details preserve every
chip's raw value, status and the chosen source.

`Ref` identifies an estimate even without a `~` prefix. Conflicting shared
references show `NA`. The expanded PWR bar uses the unrounded current/reference
ratio and labels its percentage `ref`; it is not a verified enforced-cap ratio.

## Sources and fallback

1. **Current power:** DCMI chip readings are converted as `raw × 0.1 W`.
   SMI retains its printed watt value. Missing current power stays `NA`;
   neither a model reference nor a neighboring die fills the raw field.
2. **Rated reference:** at initialization, the collector probes
   `dcmi_get_device_info`, `LP(8) / GET_POWER_INFO(10)`, and reads
   `soc_rated_power` from the 36-byte structure. For recognized 910-family
   devices, nputop **infers mW** and converts `raw / 1000` to watts.
   Unit and rated scope are assumptions, not a verified limit contract.
   Failed queries, wrong length, zero/sentinel values, unsupported families
   and implausible values are rejected. Source, raw value, return code, length
   and assumptions remain in metadata.
3. **Model fallback:** unavailable rated data uses these historical exact-model
   estimates; unknown variants remain unknown.

| Model | Reference |
| --- | --- |
| 310P1 | 90 W |
| 310P3 | 72 W |
| 910A | 310 W |
| 910B | 265 W |
| 910B1 | 430 W |
| 910B2 | 420 W |
| 910B3 | 350 W |

These are estimates, not measured limits. Core count and frequency alone cannot
account for voltage, memory, interconnect and board losses; nputop does not
scale power dynamically from those numbers.

The native dashboard excludes the old `910C = 350 W` entry because its scope
is unclear. The public `Device.power_limit()` lookup remains compatible (W),
and `power_usage()` remains mW. Native snapshot `metrics.power` remains W;
`power_reference` is separate metadata and never replaces a measured limit.

## 910C topology and uncertainty

The Huawei-authored [CloudMatrix384 paper, §3.3.1](https://arxiv.org/html/2506.12708v2#S3.SS3.SSS1)
describes two compute dies per 910C, each with 24 AIC cores and 64 GB memory.
This refers to compute dies, not every physical die in the package. DCMI's
five-element DIE identifier storage does not mean five compute dies.
nputop uses driver enumeration rather than a fixed chips-per-model rule.

Similar or slightly different die power readings do not establish measurement
scope. [Ascend-DMI documentation](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0019.html)
describes A3 tool power as card-level, but cannot prove the scope of each DCMI
reading on every firmware. Current readings remain unscaled and their scope
is marked unverified.

Separately, nputop **infers shared card scope for the rated reference** when
the model explicitly identifies `910C`, or board ID is `0xb0`–`0xb4`, using
[Ascend MindCluster's A3 IDs](https://github.com/Ascend/mind-cluster/blob/3779de1d5dd633929d21066767a6c9efedbeb937/component/ascend-common/devmanager/common/constants.go).
A generic `Ascend910` name, product code `9382`, or two chips alone is insufficient.

For example, an A3 raw rated value of `949200` becomes a **949.2 W estimated
card reference**, displayed once as `949W`. It is neither a per-die limit nor
a verified enforced cap; the value is read from the interface, not hard-coded.
The [A3 rated-query documentation](https://www.hiascend.com/doc_center/source/zh/HDK/2610/A3/A3DCMI/dcmia3_078.html)
names the field without its unit and lists 150000–600000. Because the observed
A3 value exceeds that range, nputop uses a heuristic sanity range of
150000–2000000 for identified A3, and 150000–600000 for other recognized 910
devices. These guards are not hardware specifications.

Grouping follows card/chip IDs in the complete snapshot, not adjacent display
IDs or an assumed pair. Full mode indicates partial visibility, such as
`1/2 dies`; compact mode uses `2c` for two enumerated chips. If only one A3
chip is exposed, its reference remains marked `card`, without guessing hidden
readings. Generic devices use `chips`. Visual grouping alone does not prove
a shared sensor, and ordinary per-chip references are not promoted to card scope.

System analyses such as [SemiAnalysis's CloudMatrix384 report](https://semianalysis.com/2025/04/16/huawei-ai-cloudmatrix-384-chinas-answer-to-nvidia-gb200-nvl72/)
provide context, but cannot establish a firmware field's unit or die limit.
Further hardware evidence may revise these assumptions.

## 310P telemetry

When chip power is unsupported, the collector probes the optional
`dcmi_mcu_get_power_info(card_id, &power)` card-power interface.
The 0.1 W unit follows [Ascend's MCU wrapper](https://github.com/Ascend/mind-cluster/blob/3779de1d5dd633929d21066767a6c9efedbeb937/component/ascend-common/devmanager/dcmi/dcmi.go).
Details identify card scope. If unavailable, power is `NA`; the 72 W model
reference never replaces current power. Explicit SMI mode uses only SMI output.

When overall utilization is unsupported on 310P, **UTL\*** displays AICore,
including genuine 0%. History and details identify the source. Raw JSON
`npu_overall` stays unavailable; other models and transient errors do not use
this fallback. This does not make AICore equivalent to NVML utilization.

## Expanded MBW and PWR panels

Full mode adds MBW/PWR beside MEM/UTL at 140 or more columns. At 100–139 it
keeps MEM/UTL; narrower views keep the numeric table. Compact mode shows adjacent
MEM/UTL from 124 columns, and MEM alone at 100–123. Height constraints first
compact the layout, then allow scrolling; the full inventory stays accessible.

MBW is **memory-bandwidth utilization (%)**, not memory occupancy or GB/s.
The collector reuses HBM telemetry for bandwidth and clock. When unavailable,
it probes HBM utilization selector 10 and frequency selector 6. Unsupported
HBM permits DDR selector 5 and frequency selector 1; permission or transient
errors do not justify switching counters. Memory clock is in MHz.
These counters need not share NVML's sampling definition.

[Huawei's A3 utilization documentation](https://www.hiascend.com/doc_center/source/zh/HDK/2610/A3/A3DCMI/dcmia3_055.html)
warns that passthrough-VM bandwidth can be a meaningless zero. When VM hints
are detected, HBM zero is marked unavailable with a reason; raw zero remains
in metadata. Detection is best-effort, and containers are not automatically VMs.
Other genuine zeros remain zero. Missing MBW/PWR shows `NA`; stale values
carry `*`. Explicit SMI leaves unreported fields unavailable. Chip metrics
never substitute for unavailable per-process metrics.
