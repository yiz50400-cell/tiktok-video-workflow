# Codex 商品视频工作流

客户及其团队的操作入口为 **Codex**。Codex 调用本机脚本/MCP 保存任务、素材、稿件和制作证据。当前范围到成片、独立字幕、中文说明和本机交付包结束；紫鸟、TikTok Studio、上传、草稿、挂商品和发布由员工自行手动完成。交付采用 GitHub 源码下载与本机部署，Codex 保持员工工作台。公开仓库：https://github.com/yiz50400-cell/tiktok-video-workflow

客户说明商品、市场、本次制作数量和创作要求，可同时提供素材路径、参考或文案。Codex先检查客户提供的素材，再按需求使用自带素材、补充采集或混合剪辑，完成中文稿确认、内部本地化与复核、配音剪辑和本机交付。客户明确只用自带素材时按该范围执行；素材齐备即可进入制作。默认创作方向与客户要求优先级见[项目规则](AGENTS.md)，已验证耗时与待补验收见[流程与耗时现状](docs/客户交付-流程与耗时现状.md)。


## 获取源码

```sh
git clone https://github.com/yiz50400-cell/tiktok-video-workflow.git
cd tiktok-video-workflow
```

Windows 客户请优先阅读 [docs/Windows源码部署.md](docs/Windows源码部署.md)。

## 已实现

创建/查询/取消真实任务、四国市场与语言选择、批量视频目标及创意修订、导入本地视频、SHA-256 去重、元数据与预览帧、已有分析接入和任务恢复。脚本返回本地素材和缩略图路径，Codex 对话可以直接展示。

主任务 API/CLI/MCP 已连接采集导入、分析、中文创作审计、准确中文确认、内部本地化审核、市场音色轮换、配置驱动制作、技术 QA 和本机交付。可选 LaMa 修补在隔离环境执行，须通过当前输出的视觉复核后进入后续步骤。每条保存阶段和证据，按序锁定音色，再受控并行制作；成功步骤校验哈希后复用，失败逐条恢复。素材预约关联原片区间，并核对既有本地素材台账。

Codex 负责搜索、画面观察和语义创作；自动平台搜索、ASR/OCR、多员工身份及跨机器队列待实现。其他市场须先批准对应音色池。第07步发布接入暂停。负责人字段是记录，SQLite 用于本机执行与核验。


## Codex 账号与配置（必读）

每位客户使用**自己的官方 Codex 账号**登录桌面版与命令行（`codex login`）。

- **不要**复制他人的 `~/.codex/auth.json`、`config.toml`，或任何含 bearer / API key 的文件。
- **不要**把本机 `.runtime/access.token`、`.env`、密钥提交进仓库或发给别人。
- 无人值守制作只需复制本仓库的 **ChatCut 瘦身 profile 样例**（`config/unattended-chatcut*.toml`）到自己的 `$CODEX_HOME`，再按本机路径改 `chatcut-mcp`；模型与渠道用你自己账号里已有的配置，或通过环境变量 `VW_CODEX_MODEL` / 参数 `--codex-model` 指定。
- 详细 profile 说明见 [docs/unattended-codex-profile.md](docs/unattended-codex-profile.md)；Windows 完整步骤见 [docs/Windows源码部署.md](docs/Windows源码部署.md)。

## 直接在 Codex 执行

Python 3.12+，项目独立依赖：

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt -r requirements-production.txt
.venv/bin/python scripts/workflow.py capabilities
.venv/bin/python scripts/workflow.py create --title '商品首条' --product '清洁刷'
.venv/bin/python scripts/workflow.py create --title '商品巴西批次' --product '指定商品' --market BR --count 7 --style '达人试用'
.venv/bin/python scripts/workflow.py list
.venv/bin/python scripts/workflow.py show job_实际ID
.venv/bin/python scripts/workflow.py import job_实际ID /绝对路径/商品素材.mp4
.venv/bin/python scripts/workflow.py pipeline-inputs job_实际ID /绝对路径/链路输入.json
.venv/bin/python scripts/workflow.py run job_实际ID
```

Codex 替员工执行这些命令；员工直接说商品、市场和数量，有自有素材时可补充路径。没有网页/Node/前端构建或常驻服务前置要求。可以从其他工作目录使用项目脚本与 Python 的绝对路径。

支持墨西哥（MX）、巴西（BR）、印尼（ID）、菲律宾（PH）。数量使用本次指定的1–100整数；示例命令中的7仅用于展示调用。创建时已规划/成片为0；`plan-batch` 登记实际创意，`run` 显示逐条阶段和本机交付数。菲律宾语言可在创建时指定，或用 `set-language` 后选。格式与状态见 [批量模块](docs/modules/batch.md)、[完整链路](docs/employee/pipeline.md)。

状态默认留在项目 `.runtime`，可用 `WORKFLOW_DATA_DIR` 指定独立存储。源文件不修改。输出的 `local_media_path` 和 `local_thumbnail_path` 可直接在 Codex 对话预览。

主任务恢复和交付见 [完整链路](docs/employee/pipeline.md)。单条阶段恢复见 [本地批次](docs/modules/local-batch.md)；独立本机交付包脚本见 [本地包说明](docs/modules/publishing.md)。技术 QA、真实听感/完整播放、用户成片认可和素材商用许可分别记录。

## 可选 MCP 接入

`scripts/start.sh` 仅启动本机无界面的业务 API，不执行 npm 构建。

`config/codex-mcp.example.toml` 是待合并的配置样例；全局 Codex 配置尚未改变。MCP 使用 `.runtime/access.token` 本机凭据，通过同一 API 创建/查询/导入。公司 skill 位于 `skills/team-video-workflow/SKILL.md`，现有 `mexico-tiktok-product-editor` 保持原样。

旧网页原型与 Electron 桌面壳已移入本机 `_archive/`，不进入源码仓库；Windows 安装包路线已停止（见 [历史归档](docs/Windows桌面交付-2026-10-14.md)）。默认业务服务返回 headless 状态。

## Windows 源码部署

客户通过GitHub获取源码后，在现有Codex环境中使用视频工作流。完整步骤见 [Windows源码部署](docs/Windows源码部署.md)。先安装uv及Python 3.12，在项目目录的PowerShell执行：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.lock.txt -r requirements-production.txt
.venv/Scripts/python.exe -X utf8 scripts/workflow.py capabilities
.venv/Scripts/python.exe -X utf8 scripts/check_windows_engine.py --require-windows
```

Codex根据客户需求使用本地素材、按需采集或混合制作。使用ChatCut制作时，需要本机ChatCut及其与Codex的连接可用，先读取项目状态，再进行素材与时间线操作。工作流API/MCP是可选接入，配置样例见config/codex-mcp.windows.example.toml；直接CLI方式无需常驻业务服务。

引擎诊断覆盖中文空格路径、跨进程锁、UTF-8、进程树超时清理、字体与编码解码。它使用合成短视频检查底层环境，真实商品成片另行验收。scripts/check_windows.bat可执行Windows诊断和基础回归（默认跳过 LaMa 修补测试，`scripts/check.py --with-repair` 才安装 torch/opencv）。Windows实机部署与专属进程测试仍以Windows上实际产生的报告为准。

客户素材和字体使用本机有效路径。已有方案绑定的资源须核对文件与哈希，跨机器时按实际目录重新绑定并验证。制作结果按客户任务保存，完成步骤校验后复用，缺失文件和失败步骤明确报告。当前验证记录与后续安排见[流程与耗时现状](docs/客户交付-流程与耗时现状.md)。

## 验证

```sh
.venv/bin/python scripts/check.py              # 基础回归，跳过 LaMa 修补（torch）
.venv/bin/python scripts/check.py --with-repair  # 包含修补测试，需下载 torch/opencv
```

依赖文件：`requirements.lock.txt`（运行锁定）、`requirements-production.txt`（配音与包装）、`requirements-dev.txt`（测试）、`requirements-collect.txt`（可选 yt-dlp 链接采集）、`workflow/repair/requirements.txt`（可选 LaMa 修补，隔离环境）。

测试使用临时数据库、实际 CLI/MCP/HTTP、FFmpeg 和合成短视频；完整链路工具测试使用合成音频替代联网配音。真实样片另有原批准和技术检查证据；采用独立存储核验新入口接入，不计为新的商品制作或批量产能。当前验证记录见 [链路验证](docs/完整链路验证-2026-10-14.md)。

实施顺序及每步展示方式见 `docs/阶段验收.md`。

<!-- unattended-stage1-readme -->
## 无人值守收件箱（阶段1）

阶段 1 只做本地 JSON 收件、健康检查、单并发认领和 workflow job 创建，不运行 ChatCut 制作、pipeline 或发布。incoming 也可理解为 pending。

推荐一句话投递（内部写成 JSON 再入队，无需手写文件）：

```bash
.venv/bin/python scripts/unattended.py enqueue --text '墨西哥唇釉做2条，自然带货'
echo 'Brazil lipstick 3 videos, tutorial' | .venv/bin/python scripts/unattended.py enqueue --text -
.venv/bin/python scripts/unattended.py once
.venv/bin/python scripts/unattended.py list
```

也可仍用 JSON 文件：

```bash
.venv/bin/python scripts/unattended.py enqueue --file /绝对路径/request.json
```



### 阶段2：一句话到成片（需显式 --produce）

默认 `once` **只 create**。制作必须加 `--produce`（内部 `codex exec -m 6a` + ChatCut 瘦身 profile）：

```bash
cp config/unattended-chatcut.config.toml "${CODEX_HOME:-$HOME/.codex}/unattended-chatcut.config.toml"
DEMO=$(mktemp -d /tmp/vw-demo-unattended-XXXXXX)
export WORKFLOW_DATA_DIR="$DEMO"
.venv/bin/python scripts/unattended.py enqueue --text '墨西哥唇釉做2条，自然带货'
.venv/bin/python scripts/unattended.py once --produce
.venv/bin/python scripts/unattended.py list
# 成片：$WORKFLOW_DATA_DIR/unattended/tasks/<task_id>/delivery/
```

Profile 说明见 [docs/unattended-codex-profile.md](docs/unattended-codex-profile.md)。禁止发布；自动放行会写 audit，不是假冒用户签字。

一句话需能解析出**市场**（MX/BR/ID/PH 或 墨西哥/巴西/印尼/菲律宾）和**商品**；条数可写「做2条 / 两条 / 3 videos」，缺省为 1。逗号后或 `style:` 后为风格。缺市场或缺商品会中文报错。

可选 `concepts` 必须使用与 `workflow.py plan-batch` 相同的创意对象字段，不能只写字符串。

数据根默认是 `.runtime/unattended/`，也可通过 `WORKFLOW_DATA_DIR` 覆盖。完整 schema、幂等/冲突规则及 launchd/Task Scheduler 周期调用说明见 [无人值守设计](docs/无人值守设计.md)。

