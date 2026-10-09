# nputop

**Your Ascend NPUs, at a glance.**

An interactive NPU and process monitor for the terminal. Familiar
[nvitop](https://github.com/XuehaiPan/nvitop) controls, with Ascend card/chip topology,
live resource graphs and process management.

[![PyPI](https://img.shields.io/pypi/v/ascend-nputop?logo=pypi&logoColor=white)](https://pypi.org/project/ascend-nputop/)
[![Conda](https://img.shields.io/conda/vn/conda-forge/nputop?logo=anaconda&logoColor=white)](https://anaconda.org/conda-forge/nputop)
[![Python](https://img.shields.io/badge/python-3.7%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0%20%26%20GPLv3-blue)](https://github.com/youyve/nputop/blob/main/NOTICE)

[![PyPI Downloads](https://static.pepy.tech/badge/ascend-nputop)](https://pepy.tech/project/ascend-nputop)
[![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/nputop?label=Conda%20downloads&logo=anaconda&color=orange)](https://anaconda.org/conda-forge/nputop)
[![GitHub Stars](https://img.shields.io/github/stars/youyve/nputop?label=Stars&logo=github)](https://github.com/youyve/nputop/stargazers)

**English** · [简体中文](README_zh.md) · [Installation](#installation) · [Usage](#usage) · [Supported devices](#supported-devices) · [Changelog](CHANGELOG.md)

[![nputop monitoring an eight-card, sixteen-chip Ascend 910C server running vLLM workloads](https://raw.githubusercontent.com/youyve/nputop/main/assets/nputop-910c.png)](https://raw.githubusercontent.com/youyve/nputop/main/assets/nputop-910c.png)

*Ascend 910C · 8 cards / 16 chips · DCMI · Full view. Click to see the original screenshot.*

## Why nputop?

- **See the whole machine.** NPU memory, AICore, utilization, temperature, power,
  clocks and bandwidth alongside CPU, RAM and SWAP history.
- **Find the process behind the load.** Click a process to highlight its chips;
  inspect its tree, resource history or environment, then manage it from the same screen.
- **Built for Ascend.** Single- and multi-chip cards, logical device IDs, 310P
  memory interfaces and 910C's dual-die layout get their own handling.
- **Keep your workflow.** Keyboard and mouse navigation, full/compact layouts,
  light terminals, color gradients and ASCII support.
- **Stay responsive.** DCMI collection runs outside the UI. Automatic
  `npu-smi` fallback keeps monitoring available when the primary backend fails.

## Installation

Requires **Python 3.7+**, Linux and an installed Ascend driver.
No PyACL or NVIDIA NVML Python package is needed.

### PyPI

```bash
python -m pip install --upgrade ascend-nputop
```

### Conda

Available on [conda-forge](https://anaconda.org/conda-forge/nputop):

```bash
conda install -c conda-forge nputop
```

### uv

Run in an isolated environment without installing into your project:

```bash
uvx --from ascend-nputop nputop
```

Or install it as a persistent command:

```bash
uv tool install --upgrade ascend-nputop
```

The package is `ascend-nputop` on PyPI/uv and `nputop` on Conda. All provide
the `nputop` command. [uv uses PyPI by default](https://docs.astral.sh/uv/concepts/indexes/),
so it receives the same published package.

<details>
<summary>Install from source</summary>

```bash
git clone https://github.com/youyve/nputop.git
cd nputop
python -m pip install -e .
```

</details>

## Usage

```bash
nputop
```

Start with the automatic layout, or tailor the view to your session:

```bash
nputop --monitor full
nputop --only 0 2
nputop --user
nputop --interval 1
nputop --light --colorful
nputop --readonly
```

The default interval is two seconds. `--only` selects display IDs; use
`ASCEND_RT_VISIBLE_DEVICES=0 nputop --only-visible` to filter by runtime logical IDs.

| Action | Keys |
| --- | --- |
| Help / quit | `h` / `q` |
| Select / clear selection | Click or ↑↓ / Esc or click outside the process rows |
| Auto / full / compact layout | `a` / `f` / `c` |
| Process tree / history / environment | `t` / Enter / `e` |
| Device details / mark a process | `d` / Space |
| Sort / reverse order | `s` / `/` |
| Kill / terminate / interrupt | `k/K` / `T` / `I` or Ctrl-C |

Process signals require a selected or marked target and confirmation.
As in nvitop, `k/K` requests SIGKILL; use ↑↓ or Alt-k / Alt-j to move the selection.
Ctrl-C requests SIGINT inside the TUI; use `q` to quit.

<details>
<summary>Backends, reports and compatibility options</summary>

```bash
nputop --backend dcmi
nputop --backend smi
nputop --once
nputop --json
nputop --diagnose > nputop-diagnostics.json
nputop --ascii
nputop --legacy-ui
nputop --help
```

Automatic mode prefers DCMI and falls back to `npu-smi`; an explicit backend
selection stays fixed. In 0.1.0, the new dashboard is the default;
`--legacy-ui` retains the previous UI and `--preview` remains an alias.

</details>

## Supported devices

- **Ascend 910B**
- **Ascend 910C / Atlas A3**, including dual-die cards
- **Ascend 310P**
- **Ascend 310B**, via the `npu-smi` backend

Available metrics depend on the device and driver. See
[device compatibility](docs/compatibility.md) for model-specific details.

<a id="power-estimation"></a>
AICore and UTL use distinct counters; 310P's AICore fallback is labeled `UTL*`.
Power references are labeled `Ref`, and same-card power is grouped on dual-die
devices. See [power sources and interpretation](docs/power.md).

## Contributing

Bug reports, device feedback and pull requests are welcome.
[Open an issue](https://github.com/youyve/nputop/issues) with your model and driver
version; `nputop --diagnose` produces a report without process IDs, commands or
environment contents.

For development and hardware checks, see the [contributor guide](docs/development.md).
Release maintainers can follow the [PyPI / Conda / uv release guide](docs/publishing.md).

## License and credits

Based on [nvitop](https://github.com/XuehaiPan/nvitop) by Xuehai Pan.
The project contains Apache-2.0 and GPL-3.0-only modules; consult the file headers,
[LICENSE](LICENSE), [COPYING](COPYING) and [NOTICE](NOTICE).
Maintained by [Lianzhong You](mailto:youlianzhong@gml.ac.cn).
