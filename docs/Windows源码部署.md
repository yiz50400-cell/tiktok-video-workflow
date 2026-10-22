# Windows 源码部署（当前交付方式）

更新：2026-10-21（Asia/Shanghai）。本页是 Windows 客户的唯一部署说明。交付方式为 **GitHub 源码下载 + 客户现有 Codex 作为工作台**，不提供安装包、Electron 桌面端或常驻网页。旧的桌面安装包说明见 [Windows桌面交付-2026-10-14](Windows桌面交付-2026-10-14.md)（历史归档）。

范围：到本机成片、独立字幕、中文说明和本机交付包结束。第07步发布（紫鸟、TikTok Studio、上传、草稿、挂商品、发布）不在范围内，由员工手动完成。

## 1. 前置条件

- Windows 10/11 x64，已安装并登录 **Codex 桌面版**（客户的工作台：投任务、查进度、拿成片都在这里）。
- **必装：Codex 命令行作为后台引擎**（客户日常不用打开它）。无人值守值班程序靠它调用 AI，只有桌面版时会报“未找到 Codex 运行程序”。安装：`npm i -g @openai/codex`，然后运行 `codex login`，用与桌面版**同一个账号**登录；**禁止**拷贝他人的 `auth.json` / `config.toml` 或任何 bearer 密钥到本机；装好后 `codex --version` 能输出版本即可（默认位于 `%APPDATA%\npm\codex.cmd`）。
- [uv](https://docs.astral.sh/uv/)（PowerShell：`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`）。
- Python 3.12（可由 uv 自动获取：`uv python install 3.12`）。
- **ChatCut Desktop（Windows x64）** 已安装、至少打开一次并登录同一账号。官方下载：`https://api.chatcut.io/desktop/download/windows`。制作期间保持 ChatCut 开着。
- 采集 TikTok 素材需要能访问 TikTok 的网络（客户机若需代理/VPN，请先配好；未连通时 yt-dlp 会 403/超时）。
  - **Clash Verge**：仅「规则」模式时常**不**接管 Python/yt-dlp 命令行。采集卡住并报 `TikTok 不可达` / `访问受限` 时，先切 **全局** 或打开 **TUN/系统代理**，用浏览器打开 `https://www.tiktok.com` 确认能进；仍不行再换节点/会话。也可在启动 `watch` 的同一终端设 `$env:HTTPS_PROXY="http://127.0.0.1:7897"`（端口以 Clash 混合端口为准；可用 `VW_YTDLP_PROXY` 只给 yt-dlp）。空转多轮检索修不好网络问题。
- FFmpeg **不必单独安装**：`imageio-ffmpeg` 会随 `requirements.lock.txt` 带上；yt-dlp 在装了 `requirements-collect.txt` 后可用。
- 配音（edge-tts）需要可用网络。

项目目录可以包含中文和空格，下文以 `<项目目录>` 表示，例如 `D:\视频 工作流`。

> **尚未在目标 Windows 实机验收**：下列步骤与脚本已按 Windows 差异改过，但本仓库维护机是 macOS，不能用 Mac 结果替 Windows 平台验收。

## 2. 安装依赖

在 `<项目目录>` 打开 PowerShell：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.lock.txt -r requirements-production.txt
```

可选依赖：

```powershell
# 按链接解析/下载参考视频（yt-dlp）
uv pip install --python .venv/Scripts/python.exe -r requirements-collect.txt
# 含 yt-dlp 和 curl_cffi（模拟浏览器访问 TikTok）。装完可用下面命令确认，列表里应出现 curl_cffi：
# .venv\Scripts\yt-dlp.exe --list-impersonate-targets
# 运行测试
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
```

LaMa 局部修补（torch/opencv，体积数 GB）不属于基础安装，需要时按 `docs/modules/repair.md` 在隔离环境中使用。

## 3. 验证

```powershell
.venv/Scripts/python.exe -X utf8 scripts/workflow.py capabilities
.venv/Scripts/python.exe -X utf8 scripts/check_windows_engine.py --require-windows
```

或直接运行 `scripts\check_windows.bat`：先执行 Windows 引擎诊断（中文空格路径、跨进程锁、UTF-8、进程树超时清理、字体与编解码），再执行基础回归。基础回归默认跳过 LaMa 修补测试；需要时运行 `.venv/Scripts/python.exe scripts/check.py --with-repair`。

诊断报告写入 `.runtime/windows-engine-check/`。只有在 Windows 上实际产生的报告才算 Windows 平台验收，macOS 结果不能替代。

## 4. 日常使用（默认 = 无人值守）

分工：**Codex 桌面版是工作台，后台引擎干活**。**默认就是无人值守**，不要把一句话任务带进对话逐步审中文稿 / 葡语稿 / 音色试听。

| 客户说法 | Codex 必须做 |
| --- | --- |
| 「启动视频工作流」 | 确认 ChatCut 已打开；接通电源时不睡眠（见 §5.2）；在项目目录后台启动 `.venv\Scripts\python.exe -X utf8 scripts\unattended.py watch`（每 30 秒扫收件箱）。**不要设开机自启。** |
| 一句话任务（可多条） | `.venv\Scripts\python.exe -X utf8 scripts\unattended.py enqueue --text "..."`；由 `watch` 制作，或单次验证用 `once --produce` |
| 「做完了吗」 | `unattended.py list`；成片在各任务 `delivery/` |

```powershell
# 启动值班（「启动视频工作流」）
.venv\Scripts\python.exe -X utf8 scripts\unattended.py watch

# 投任务（可连续多条）
.venv\Scripts\python.exe -X utf8 scripts\unattended.py enqueue --text "巴西润唇膏做3条，真人试用种草，无自带素材"
.venv\Scripts\python.exe -X utf8 scripts\unattended.py enqueue --text "墨西哥唇釉做1条，自然带货"

# 单次验证（可选；正式用 watch 即可）
.venv\Scripts\python.exe -X utf8 scripts\unattended.py once --produce

.venv\Scripts\python.exe -X utf8 scripts\unattended.py list
```

投完即可关闭对话离开。每条任务做好或失败时，值班程序会在 Windows 右下角弹**一次**系统通知（任务号与成片位置或失败原因）；不想要提醒可设 `WORKFLOW_NOTIFY=0`。

**禁止默认**：在 Codex 对话里展示中文稿等确认、当地语言全文确认、音色试听页确认、代表样片逐步批准。仅当客户**明确要求**对话精修时，才使用下面的 `workflow.py` 交互链路。

### 4.1 高级：对话精修 / 直接 CLI（非默认）

```powershell
.venv\Scripts\python.exe -X utf8 scripts\workflow.py create --title "商品巴西批次" --product "指定商品" --market BR --count 7 --style "达人试用"
.venv\Scripts\python.exe -X utf8 scripts\workflow.py list
.venv\Scripts\python.exe -X utf8 scripts\workflow.py show job_实际ID
.venv\Scripts\python.exe -X utf8 scripts\workflow.py import job_实际ID "D:\素材\商品素材.mp4"
.venv\Scripts\python.exe -X utf8 scripts\workflow.py pipeline-inputs job_实际ID "D:\任务\链路输入.json"
.venv\Scripts\python.exe -X utf8 scripts\workflow.py run job_实际ID
```

直接 CLI 不需要常驻服务。状态默认 `<项目目录>\.runtime`，可用 `WORKFLOW_DATA_DIR`。交互链路细节见 [docs/employee/pipeline.md](employee/pipeline.md)。

## 5. 可选：MCP 接入

参考 `config/codex-mcp.windows.example.toml`，把 `<项目目录>` 替换为实际路径后合并到员工自己的 Codex 配置。MCP 通过本机 API 工作，使用前在项目目录运行 `scripts\start.bat` 启动本机服务（仅监听 127.0.0.1:8765）。

## 5.1 首次部署必做：ChatCut MCP 门禁 + 金丝雀制作

无人值守制作依赖 Codex 会话真正“看见” `chatcut_desktop` MCP 工具；看不到时 Codex 会回退到 Computer Use / ChatCut 应用内 AI Agent，消耗 credits。首次部署（以及每次升级 ChatCut 或 Codex 后）必须：

1. 安装无人值守 ChatCut profile（**不要照抄 macOS 的 Library 路径**）：
   - 复制 `config/unattended-chatcut.windows.example.toml` → `%USERPROFILE%\.codex\unattended-chatcut.config.toml`（若设置了 `CODEX_HOME` 则用 `%CODEX_HOME%\unattended-chatcut.config.toml`）。
   - 把 `[mcp_servers.chatcut_desktop] command` 改成该机真实的 `chatcut-mcp` 绝对路径。常见位置：`%LOCALAPPDATA%\ChatCut\`、`%LOCALAPPDATA%\Programs\ChatCut\`、`%APPDATA%\ChatCut\`（扩展名可能是 `.exe` / `.cmd`）。也可用环境变量 `CHATCUT_MCP` 指向同一文件。
   - `[mcp_servers.chatcut_desktop.env] CHATCUT_MCP_CLIENT` 必须是 ChatCut 白名单内的值，推荐 `codex_app`。`codex_unattended`、`codex_cli` 等非白名单值会让 chatcut-mcp 启动即 `fatal: CHATCUT_MCP_CLIENT is invalid`，Codex 将拿不到任何 ChatCut 工具。
   - `[features] code_mode_host = true`（gpt-5.6-sol 默认延迟加载 MCP，关闭后工具不会进入模型工具面）；`computer_use = false`。
   - **Windows 不会自动重启 ChatCut**：挂起时请手动退出再打开 ChatCut，再跑健康探针；不要依赖 `chatcut_health.py --apply`（Windows 上会明确报未实现）。
2. 运行门禁（只读，不耗 credits）：
   ```bat
   .venv\Scripts\python.exe -X utf8 -c "from workflow.unattended.codex_mcp_gate import verify_codex_chatcut_mcp_ready as v; print(v())"
   ```
   必须输出 `ok=True` 且 `tools` ≥ 50；否则先修配置，不要开始制作。producer 每次制作前也会自动执行该门禁并 fail-fast。
3. 跑一条短金丝雀制作：确认 ChatCut 中已有工程 `TEST-无人值守-20261017`（没有就先建同名本地工程）。先投递任意一句话，再 `.venv\Scripts\python.exe scripts\unattended.py once --produce --canary`（只导出该测试工程，不采集；启动方式与正式制作相同：`codex exec -p unattended-chatcut -m gpt-5.6-sol -s danger-full-access --enable code_mode_host --disable computer_use`）。验收：
   - Codex 日志出现 `mcp: chatcut_desktop/...` 调用，且无 Computer Use / 应用内 AI 痕迹（ChatCut `logs/agent.log` 无新的 `Agent SDK attempt started`）；
   - 导出 mp4 存在且可播放；
   - ChatCut credits 前后不变。
   金丝雀工程保留归档，不要删除。
4. 金丝雀通过后再跑一条正式端到端：`scripts\unattended.py enqueue --text "墨西哥<商品>做1条，TikTok种草风格"` → `once --produce`（默认 production 模式）。流水线依次为：ChatCut 健康探针 → MCP 门禁 → 采集（Codex web search 找 TikTok 视频页 → yt-dlp 下载 → 竖屏/时长/来源校验 → 联系表）→ Codex 直连 MCP 剪辑导出 → 成片校验（竖屏 H.264、8–60 秒、无口播必须静音、只用采集清单内素材、不是 TEST 工程、无 submit_*/Computer Use）。采集后还有视觉初筛（同款形态/原片烧录文字/水印/生活方式镜头），导出后逐镜头自动复核，不合格自动重剪最多 2 次，仍不合格不交付。采集需要能访问 TikTok 的网络（客户机若需代理请先配好）；合格素材少于 3 条时直接失败，不会用库存/样例素材顶替。同一类商品做过的成片会记进 `config/discovery-seeds/used/` 台账，下次优先找没用过的新素材，新素材不够才用旧素材补位。

## 5.2 关闭自动睡眠（无人值守必做）

无人值守任务一次要跑十几到几十分钟，电脑睡眠/休眠会让 Codex、ChatCut 和下载中途断掉（我们在 Mac 上实测过：机器睡眠后任务直接失联）。客户机在**接通电源时**按下面设置（管理员 PowerShell / CMD）：

```bat
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 30
```

- 前两条：接通电源时永不睡眠、永不休眠（屏幕可以关，关屏不影响任务）。
- 笔记本合盖：控制面板 → 电源选项 → “选择关闭盖子的功能” → “接通电源”一栏选 **不采取任何操作**。也可用命令：
  `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0` 然后 `powercfg /setactive SCHEME_CURRENT`
- 验证：`powercfg /query SCHEME_CURRENT SUB_SLEEP` 中 AC 值应为 0x00000000。
- 只改接通电源（AC）设置；电池模式保持系统默认。不设置开机自启。

### Codex 账号：用客户自己的正式账号

- 客户机上的 Codex 桌面版和 Codex CLI 都登录**客户自己的正式 Codex 账号**（两者同一账号）。
- **不要**从我们开发机拷贝 `~/.codex/config.toml`、`auth.json` 或任何第三方/中转接口配置（base_url、bearer key 等）；客户机只用官方登录产生的配置。
- 模型默认 `gpt-5.6-sol`，不强制；账号不支持（例如报 404 / 503 无可用渠道）时，直接换成账号可用的模型即可，不需要改代码：
  - 仅当前窗口生效：`set VW_CODEX_MODEL=gpt-5.6-astra`（PowerShell：`$env:VW_CODEX_MODEL="gpt-5.6-astra"`）
  - 永久生效：`setx VW_CODEX_MODEL gpt-5.6-astra`，然后**关闭并重新打开**命令行窗口
  - 单次运行临时指定：在命令后加 `--codex-model gpt-5.6-astra`
  - 优先级：`--codex-model` > 环境变量 `VW_CODEX_MODEL` > 默认 `gpt-5.6-sol`；视觉初筛、成片复核、剪辑都用同一个模型
  - 恢复默认：`setx VW_CODEX_MODEL ""` 或在“系统属性 → 环境变量”中删除该变量

## 6. 客户数据与资源

- 音色池：每个市场须先完成音色试听批准（**建池时**，不是每条无人值守任务都在对话里试听）；制作配置中的 `voice_approval` 使用本机批准文件的绝对路径和 SHA-256。开发样片的巴西音色批准不随源码分发。
- 字体：包装配置使用本机字体文件的绝对路径和 SHA-256，不依赖 macOS 系统字体。
- 编码器：Windows 使用 `libx264`；`h264_videotoolbox` 仅限 macOS。
- 跨机器迁移已有方案时，按实际目录重新绑定资源并核对哈希。

## 7. 已知 Windows 差异（实机前必读）

| 项 | 状态 |
| --- | --- |
| Python 引擎 / 中文空格路径 / UTF-8 / 文件锁 / Job Object 超时清理 | 代码已适配；须在 Windows 上跑 `scripts\check_windows.bat` 才算通过 |
| FFmpeg | 走 `imageio-ffmpeg`，Windows 默认 `libx264`（拒绝 `h264_videotoolbox`） |
| yt-dlp 采集 | 需 `requirements-collect.txt`（含 yt-dlp、curl_cffi）+ 可访问 TikTok 的网络 |
| ChatCut MCP 探针 / Codex 门禁 | 已支持按 `CHATCUT_MCP` 与常见 `%LOCALAPPDATA%` 路径查找；stdio JSON-RPC 在 Windows 上不再依赖 `select()` 管道 |
| ChatCut **自动重启** | **未实现**（会 `NotImplementedError`）；无人值守前请保持 ChatCut 打开 |
| 应用内 AI Agent 违规检测日志 | Windows 默认读 `%LOCALAPPDATA%\ChatCut\logs\agent.log` |
| 正式端到端（采集→剪辑→导出） | **未在 Windows 实机跑过** |

## 8. 客户机最小金丝雀（首次部署必做）

在 `<项目目录>`、ChatCut 已打开且已登录时。金丝雀会导出名为 `TEST-无人值守-20261017` 的工程：若客户机还没有，先在 ChatCut 新建或复制一个**同名**本地工程（可放任意短竖屏素材），不要删除；没有该工程时 `--canary` 会失败。

```bat
.venv\Scripts\python.exe -X utf8 scripts\check_windows_engine.py --require-windows
.venv\Scripts\python.exe -X utf8 -c "from workflow.chatcut.health import probe_chatcut; print(probe_chatcut().to_dict())"
.venv\Scripts\python.exe -X utf8 -c "from workflow.unattended.codex_mcp_gate import verify_codex_chatcut_mcp_ready as v; print(v())"
.venv\Scripts\python.exe -X utf8 scripts\unattended.py once --produce --canary
```

验收：引擎报告 `windows_platform_executed=true`；健康探针 `status=healthy`；门禁 `ok=True` 且 `tools`≥50；金丝雀导出 mp4 可播放；ChatCut credits 前后不变；Codex 日志有 `mcp: chatcut_desktop/...`，无应用内 AI。金丝雀通过后再跑一句正式任务。

## 9. 待 Windows 实机验证

源码部署、依赖安装、ChatCut 本机连接、中文路径媒体迁移、原生导出、缺文件选择性恢复，以及上一节金丝雀 + 一条正式短片，均需在目标 Windows 电脑上实测并保存报告到 `.runtime/windows-engine-check/`。Mac 上的通过记录不能替代。
