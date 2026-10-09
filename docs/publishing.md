# Coordinated releases / 多渠道同步发布

Release each version through PyPI and conda-forge in the same release cycle. uv
installs the PyPI distribution; it does not require a separate package upload.
Announce the release after the same version is available through all three
installation routes. Index refresh and conda-forge CI timings are independent.

每个版本按同一发布批次同步推进 PyPI 与 conda-forge；uv 使用 PyPI 发行包，无需单独
上传。三个安装入口均可获取同一版本后再发布公告。索引刷新和 conda-forge CI 耗时各自独立。

| Route / 入口 | Package / 包名 | Release source / 发行来源 |
| --- | --- | --- |
| pip / PyPI | `ascend-nputop` | Wheel and source distribution / wheel 与源码包 |
| Conda | `conda-forge::nputop` | [nputop-feedstock](https://github.com/conda-forge/nputop-feedstock) |
| uv / uvx | `ascend-nputop` | Same PyPI package / 同一 PyPI 包 |

## Prepare once / 统一准备

Run the [contributor checks](development.md), review hardware compatibility,
and audit the files and history to be published. Keep raw validation reports
outside the repository. The commands below use 0.1.0 as the release example.

完成[贡献指南中的检查](development.md)，复核硬件兼容性，并审阅待发布文件与提交
历史。原始验证报告保留在仓库外。以下命令以 0.1.0 为例。

```bash
# From the repository root: remove only generated build metadata.
rm -rf build *.egg-info
python -m build
python tools/check_distribution.py dist/*.whl dist/*.tar.gz
python -m twine check dist/*.whl dist/*.tar.gz
python tools/prepare_conda_recipe.py dist/ascend_nputop-0.1.0.tar.gz --output dist/conda/meta.yaml
```

Prefer a clean checkout. When reusing a working tree, clear generated `build/`
and `*.egg-info` first: cached `SOURCES.txt` can retain files from older releases.
The manifest prunes documentation, tools and assets before applying the public
file list, and the archive check rejects unexpected contents.

建议从干净检出构建。复用工作目录时，先清理自动生成的 `build/` 和 `*.egg-info`，
避免旧 `SOURCES.txt` 带入历史文件。清单会先排除文档、工具与图片目录的缓存项，
再应用公开文件列表；归档检查会拒绝额外内容。

License metadata retains the syntax supported by Python 3.7 build tools.
Migration to PEP 639 needs a separate compatibility change: setuptools introduced
SPDX expressions and `project.license-files` in
[77.0.0](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html).

许可证元数据暂保留 Python 3.7 构建工具支持的语法。PEP 639 迁移需单独处理构建
兼容性：SPDX 表达式及 `project.license-files` 从 setuptools 77.0.0 才开始支持。

The helper reads the version, Python requirement and runtime dependency minima
from the source archive's metadata and hashes that exact archive. It generates
a recipe for the existing `nputop` feedstock, with both licenses and genuine
no-driver import/help/version checks. It does not upload or open a PR.
Generate it outside the source archive to avoid a self-referential checksum.

工具从源码包元数据读取版本、Python 要求与运行依赖下限，并计算该源码包的 SHA256。
生成的配方用于现有 `nputop` feedstock，包含两种许可证以及无需驱动的导入、帮助、版本
检查；不上传文件，也不创建 PR。配方生成在源码包外，避免校验值自引用。

Prepare a pull request from a personal fork of the feedstock, following the
conda-forge update workflow. Use the **same PyPI source archive** as the Python
release. Do not combine a GitHub archive URL with the PyPI archive's hash.
Keep the feedstock's platform configuration; its Linux builds must pass CI.

按 conda-forge 流程，在个人 fork 中准备 feedstock 更新并提交 PR。
配方使用本次发布的**同一份 PyPI 源码包**；不能混用 GitHub 归档 URL 与
PyPI 源码包的校验值。
保留 feedstock 平台配置，并完成 Linux 构建 CI。

## Publish and verify / 发布与核对

1. Merge the reviewed commit and screenshot asset into `main`, and tag `v0.1.0`.
   Open the README screenshot URL and confirm it loads before uploading to PyPI;
   the URL points to `main`, not the release branch.
2. Upload the verified wheel and source archive to PyPI. Then run the feedstock
   CI against the now-available source URL and merge the reviewed recipe update.
3. Check all three installation routes before the release announcement.

1. 将已审阅提交及截图资源合并到 `main`，创建 `v0.1.0` 标签。上传 PyPI 前打开
   README 截图 URL，确认能够加载；该链接指向 `main`，而非发布分支。
2. 上传通过检查的 wheel 和源码包到 PyPI；源码 URL 可用后完成 feedstock CI，合并配方更新。
3. 发布公告前，检查三个安装入口均能安装 0.1.0。

Run the following in separate disposable environments after publication:
发布后在各自独立的临时环境中执行：

```bash
python -m pip install 'ascend-nputop==0.1.0'
nputop --version

conda create -n nputop-release-check -c conda-forge 'nputop=0.1.0'
conda run -n nputop-release-check nputop --version

uvx --refresh --from 'ascend-nputop==0.1.0' nputop --version
```

Each must report `nputop 0.1.0`. The version and download badges track their
respective registries automatically; download counts are not unique-user counts.

三者均应输出 `nputop 0.1.0`。版本与下载量徽章自动跟踪对应渠道；下载次数不等于独立用户数。

References / 参考：[conda-forge updates](https://conda-forge.org/docs/maintainer/updating_pkgs/),
[uv tool packages](https://docs.astral.sh/uv/guides/tools/#commands-with-different-package-names),
[uv's default index](https://docs.astral.sh/uv/concepts/indexes/).
