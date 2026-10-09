# Development / 开发

Install a source checkout with the existing runtime dependencies and pytest:
从源码安装开发版本及测试工具：

```bash
git clone https://github.com/youyve/nputop.git
cd nputop
python -m pip install -e . pytest
python -m pytest -q
```

The default dashboard consumes snapshots from an isolated collector. Keep driver
calls out of the UI, preserve public API and JSON contracts, and support Python
3.7 and the declared dependency minima. See [compatibility contracts](compatibility.md).

默认界面只消费独立采集进程的快照。请保持 UI 与驱动调用分离，保留公共 API 和 JSON
字段契约，兼容 Python 3.7 及声明的依赖下限。后端与设备编号规则见
[兼容性文档](compatibility.md)。

Use nvitop's information hierarchy and navigation as the UI baseline while
keeping Ascend card/chip identity and metric meanings. Selection brightens
associated chips and dims others; device backgrounds and bar colors stay intact.
Shrinking a terminal compacts the layout before scrolling the full inventory.
Check narrow/wide terminals, Unicode/ASCII, long commands and missing metrics.

界面沿用 nvitop 的信息层级与导航习惯，保留昇腾卡／芯片身份及指标语义。
选择进程时加亮关联芯片、适当降低其他芯片亮度，保持设备背景与条形颜色。
终端缩小时先压缩布局，再滚动完整设备清单。请检查窄屏、宽屏、Unicode、ASCII、
长命令和缺失指标的显示。

## Hardware feedback / 硬件反馈

```bash
python tools/acceptance.py --output acceptance.json
python -m nputop --readonly
python -m nputop --backend smi --readonly
```

The acceptance tool is read-only by default, requires no root and uploads
nothing. Its report excludes process IDs, usernames, commands, environments,
hostname and PCI addresses. Send it with the device model and driver version.
`--signal-test` is optional and targets only a child created by the tool;
`--benchmark 10` measures first-frame and exit latency.

验收工具默认只读，无需 root，也不上传数据；报告排除进程 PID、用户名、命令、环境变量、
主机名和 PCI 地址。提交时请附设备型号和驱动版本。可选的 `--signal-test` 只操作工具自己
创建的子进程；`--benchmark 10` 测量首帧与退出耗时。

## Package checks / 安装包检查

```bash
python -m pip install build twine
python -m build
python tools/check_distribution.py dist/*.whl dist/*.tar.gz
python -m twine check dist/*.whl dist/*.tar.gz
```

Run installed-wheel regressions outside the source checkout so imports cannot
accidentally use local sources. CI covers Python 3.7–3.14 and minimum dependencies
on 3.7. Hardware checks complement these offline tests. Channel coordination
is in the [publishing guide](publishing.md).

安装包回归需在源码目录外执行，避免误用本地源码。CI 配置覆盖 Python 3.7–3.14 及 3.7
最低依赖组合，实机检查用于补充离线测试。渠道同步见[发布指南](publishing.md)。

Keep local logs, raw snapshots, server access details and development notes
outside the repository. Before opening a PR, review the complete diff and its
commit history. The source distribution includes only the public documentation
and tools listed in `MANIFEST.in`; update the archive check when adding one.

本地日志、原始快照、服务器连接信息和开发记录应存放在仓库外。提交 PR 前请审阅
完整差异和提交历史。源码包只收录 `MANIFEST.in` 列出的公开文档与工具，新增时
应同步更新归档检查。
