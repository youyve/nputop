# nputop

**昇腾 NPU 的运行状态，一目了然。**

面向终端的交互式 NPU 与进程监控工具。延续
[nvitop](https://github.com/XuehaiPan/nvitop) 的操作习惯，结合昇腾卡／芯片拓扑，
在一个界面中查看实时负载、资源趋势和进程状态。

[![PyPI](https://img.shields.io/pypi/v/ascend-nputop?logo=pypi&logoColor=white)](https://pypi.org/project/ascend-nputop/)
[![Conda](https://img.shields.io/conda/vn/conda-forge/nputop?logo=anaconda&logoColor=white)](https://anaconda.org/conda-forge/nputop)
[![Python](https://img.shields.io/badge/python-3.7%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0%20%26%20GPLv3-blue)](https://github.com/youyve/nputop/blob/main/NOTICE)

[![PyPI Downloads](https://static.pepy.tech/badge/ascend-nputop)](https://pepy.tech/project/ascend-nputop)
[![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/nputop?label=Conda%20downloads&logo=anaconda&color=orange)](https://anaconda.org/conda-forge/nputop)
[![GitHub Stars](https://img.shields.io/github/stars/youyve/nputop?label=Stars&logo=github)](https://github.com/youyve/nputop/stargazers)

[English](README.md) · **简体中文** · [安装](#安装) · [使用](#使用) · [支持的设备](#支持的设备) · [更新日志](CHANGELOG.md)

[![nputop 在运行 vLLM 任务的八卡十六芯片昇腾 910C 服务器上的实机界面](https://raw.githubusercontent.com/youyve/nputop/main/assets/nputop-910c.png)](https://raw.githubusercontent.com/youyve/nputop/main/assets/nputop-910c.png)

*昇腾 910C · 8 卡 / 16 芯片 · DCMI · 完整视图。点击查看原始截图。*

## 为什么选择 nputop？

- **整机状态，一屏掌握。** 同时查看 NPU 内存、AICore、利用率、温度、功耗、
  时钟与带宽，以及 CPU、RAM、SWAP 历史趋势。
- **从负载找到进程。** 点击进程即可加亮关联芯片，继续查看进程树、资源历史或
  环境变量，并在同一界面管理进程。
- **贴合昇腾硬件。** 适配单／多芯片卡、运行时逻辑编号、310P 内存接口和
  910C 双 die 拓扑。
- **熟悉的操作习惯。** 支持键盘与鼠标、完整／紧凑布局、浅色终端、渐变配色和
  ASCII 字符。
- **交互保持流畅。** DCMI 在独立进程中采集；主后端异常时自动回退到
  `npu-smi`，继续提供监控。

## 安装

需要 **Python 3.7+**、Linux 和已安装的昇腾驱动。
无需安装 PyACL 或 NVIDIA NVML Python 包。

### PyPI

```bash
python -m pip install --upgrade ascend-nputop
```

### Conda

通过 [conda-forge](https://anaconda.org/conda-forge/nputop) 安装：

```bash
conda install -c conda-forge nputop
```

### uv

在独立环境中直接运行，不占用项目环境：

```bash
uvx --from ascend-nputop nputop
```

也可以安装为长期使用的命令：

```bash
uv tool install --upgrade ascend-nputop
```

PyPI／uv 包名为 `ascend-nputop`，Conda 包名为 `nputop`，启动命令均为
`nputop`。[uv 默认使用 PyPI](https://docs.astral.sh/uv/concepts/indexes/)，
获取的是同一个已发布安装包。

<details>
<summary>从源码安装</summary>

```bash
git clone https://github.com/youyve/nputop.git
cd nputop
python -m pip install -e .
```

</details>

## 使用

```bash
nputop
```

默认按终端大小自动布局，也可根据需要调整：

```bash
nputop --monitor full
nputop --only 0 2
nputop --user
nputop --interval 1
nputop --light --colorful
nputop --readonly
```

默认刷新间隔为两秒。`--only` 按显示编号筛选；按运行时逻辑编号筛选可使用
`ASCEND_RT_VISIBLE_DEVICES=0 nputop --only-visible`。

| 操作 | 按键 |
| --- | --- |
| 帮助／退出 | `h` / `q` |
| 选择／取消选择 | 鼠标点击或 ↑↓ / Esc 或点击进程行以外的区域 |
| 自动／完整／紧凑布局 | `a` / `f` / `c` |
| 进程树／资源历史／环境变量 | `t` / Enter / `e` |
| 设备详情／标记进程 | `d` / 空格 |
| 切换排序／反转顺序 | `s` / `/` |
| 强制结束／终止／中断进程 | `k/K` / `T` / `I` 或 Ctrl-C |

发送信号前必须选中或标记目标，并经过确认。
与 nvitop 一致，`k/K` 请求 SIGKILL；移动选择请用 ↑↓ 或 Alt-k / Alt-j。
TUI 内 Ctrl-C 请求 SIGINT；退出请用 `q`。

<details>
<summary>后端、报告与兼容选项</summary>

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

自动模式优先 DCMI，异常时回退 `npu-smi`；显式指定后端时不切换。
0.1.0 默认使用新界面，`--legacy-ui` 保留旧界面，`--preview` 保留为兼容别名。

</details>

## 支持的设备

- **昇腾 910B**
- **昇腾 910C / Atlas A3**，支持双 die 卡
- **昇腾 310P**
- **昇腾 310B**，通过 `npu-smi` 后端

具体可用指标取决于设备和驱动，型号适配说明见[设备兼容性文档](docs/compatibility.md)。

<a id="power-estimation"></a>
AICore 与 UTL 使用不同计数器；310P 回退到 AICore 时标注 `UTL*`。
功耗参考值标注 `Ref`，双 die 设备按卡归组显示功耗。
详见[功耗来源与读数说明](docs/power_zh.md)。

## 参与贡献

欢迎提交问题、设备反馈和改进。可在
[Issues](https://github.com/youyve/nputop/issues) 中附上型号和驱动版本；
`nputop --diagnose` 生成的报告不包含进程 PID、命令或环境变量内容。

开发与硬件检查见[贡献指南](docs/development.md)，
版本维护见 [PyPI / Conda / uv 发布指南](docs/publishing.md)。

## 许可证与致谢

基于 Xuehai Pan 的 [nvitop](https://github.com/XuehaiPan/nvitop) 项目。
项目包含 Apache-2.0 与 GPL-3.0-only 模块，具体适用许可见各文件头部、
[LICENSE](LICENSE)、[COPYING](COPYING) 和 [NOTICE](NOTICE)。
维护者：[Lianzhong You](mailto:lyou593@connect.hkust-gz.edu.cn)。
