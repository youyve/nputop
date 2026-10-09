# Changelog / 版本变更

## 0.1.0

### English

- Restore Ascend device enumeration and process collection in the public
  `take_snapshots` and `ResourceMetricCollector` APIs, preserving NPU metric
  names, units and missing values.
- Stop idle host-inspection workers after parent exit, including terminal
  disconnects. Limit idle redraws while keeping input and new data responsive.
- Make the native dashboard the default; keep `--legacy-ui` and the
  `--preview` compatibility alias. The normal interval remains two seconds.
- Prefer isolated, serial DCMI collection with automatic SMI fallback; explicit
  backend selections remain fixed. Preserve missing, stale and zero readings.
- Add process tree, metrics and environment pages, keyboard/mouse navigation,
  multi-selection and confirmed, identity-checked signal operations.
- Restore nvitop-like device blocks, full/compact/auto layouts, mirrored history,
  process grouping and associated-chip highlighting without reverse backgrounds.
  Resize compacts before scrolling and retains the complete device inventory.
- Add optional memory bandwidth/clock and reference-labeled PWR panels. Shared
  card power appears on the first visible die; raw values/precision remain in
  details and JSON. Document the inferred 910C reference scope and units.
- Support enumerated single/multiple-chip layouts, 310P DDR/MCU capabilities,
  and verified logical-ID filtering without treating list positions as IDs.
- Use one formal version for CLI/TUI/distributions, independent of Git tags.
  Consolidate build metadata and include licenses, both READMEs, tests and
  read-only acceptance tools in the source distribution.
- Keep Python >=3.7, existing runtime dependency minima and public Python APIs.
  Clarify that Ctrl-C requests SIGINT inside the native dashboard; use `q` to quit.
- Fix first runtime fallback on clocks with a near-zero origin, retaining retry
  cooldowns; return a clear error when the legacy UI has no accessible devices.
- Reject invalid display indices consistently before one-shot/JSON/TUI output;
  an explicitly empty runtime-visibility list remains a valid empty selection.

### 中文

- 修复公共 `take_snapshots` 与 `ResourceMetricCollector` API 的昇腾设备枚举和
  进程采集，保留 NPU 指标名称、单位及缺失值。
- 父进程退出（包括终端断开）后清理空闲主机检查子进程；降低空闲重绘频率，
  保持键鼠和新数据的及时响应。
- 新界面成为默认入口，保留 `--legacy-ui` 及 `--preview` 兼容别名；
  正常采样间隔仍为两秒。
- 优先使用独立进程内串行执行的 DCMI 采集，自动模式支持 SMI 回退；显式指定后端
  不静默切换。保留缺失、过期与真实零值的区别。
- 补齐进程树、指标和环境变量页面、键鼠导航、多选，以及经过身份检查和确认的信号操作。
- 采用接近 nvitop 的设备分区、完整／紧凑／自动布局、对称历史图、进程分组和关联芯片
  加亮显示，设备背景不反白。缩放先压缩布局再滚动，完整设备清单保持可达。
- 新增可选内存带宽／时钟及标注 `ref` 的 PWR 面板。同卡功耗在首个可见 die 显示，
  详情与 JSON 保留原值和精度；明确记录 910C 参考值单位及范围的推测。
- 按枚举适配单／多芯片布局、310P DDR／MCU 能力及经过验证的逻辑编号筛选，
  不把列表位置当作设备编号。
- CLI、TUI、发行包共用正式版本号，不再依赖 Git 标签。统一打包元数据，源码包包含
  许可证、中英文 README、测试及默认只读的验收工具。
- 保留 Python >=3.7、现有依赖最低版本及公共 Python API。明确新 TUI 内 Ctrl-C
  请求 SIGINT，退出使用 `q`。
- 修复时钟起点接近零时的首次运行时回退，同时保留重试间隔；旧界面无法访问设备时
  明确报错，不再返回成功状态。
- 单次、JSON 和 TUI 入口统一拒绝无效显示编号；显式空的运行时可见列表仍表示合法空选择。

See [device compatibility / 设备兼容性](docs/compatibility.md).
