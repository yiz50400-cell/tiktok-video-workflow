---
name: team-video-workflow
description: "Default path is Codex desktop produce: open Codex desktop + ChatCut, one-sentence task, deliver to delivery/. Do not default into watch/CLI background duty or interactive script/voice review. Platform upload/publishing stay manual."
---

# 公司视频工作流 · 本机制作与交付

## ChatCut MCP

- ChatCut MCP 健康检查与参数约定：[chatcut-mcp.md](chatcut-mcp.md)


Codex 桌面版是客户工作台。**默认路径是桌面版出片**（开桌面版 + ChatCut，一句话做到 `delivery/`），**不是**后台 `watch` / 日常 CLI，也不是对话里逐步审稿。当前工作流到成片、独立字幕、中文说明和本机交付包结束；紫鸟、TikTok Studio、上传、草稿、挂商品和发布均由员工自行手动完成。

## 默认路径：Codex 桌面版出片（必须先读）

收到下列说法时，**在桌面会话里直接制作，禁止挂后台 watch，禁止默认进入员工交互审稿/试听**：

| 客户说法 | 你要做的事 |
| --- | --- |
| 「启动视频工作流」 | 确认 ChatCut 已打开；提醒接通电源时不睡眠；确认一次性安装与 ChatCut MCP 已就绪。**不要**后台启动 `unattended.py watch`，**不要设开机自启。** |
| 一句话任务（可连续多条） | 在本桌面会话按仓库规则制作；成片写入各任务 `delivery/` |
| 「做完了吗」/查进度 | 打开对应任务的 `delivery/`，或在本会话报告进度 |

硬规则（任何路径都适用）：

- 无 BGM（无口播→静音；有口播→只保留口播）。
- 剪辑只用 Codex→ChatCut MCP；**禁止** ChatCut 应用内 AI、**禁止** Computer Use、**禁止** `submit_*`。
- 使用客户自己的 Codex 桌面登录与配置；**禁止**拷贝他人 `auth.json` / `config.toml` / 密钥。
- **禁止默认**展示中文稿等确认、当地语言全文确认、音色试听确认、代表样片风格逐步批准。桌面出片自动放行写 audit。
- 仅当客户**明确说**要改稿、挑音色、确认风格或「走对话精修」时，才使用下文 `workflow.py` / pipeline 交互步骤。
- `unattended.py watch` / `enqueue` / `once --produce` / `codex exec` 仅**可选调试**（排障、金丝雀）；客户未要求时不要当作默认。

## 默认业务合同

客户提供商品、市场、本次制作数量和创作要求。执行时优先本次要求，再沿用该客户已确认的长期偏好，未指定部分使用项目默认。**默认**在 Codex 桌面版完成素材检查/补充、内部本地化与复核、配音剪辑并交付本机文件；客户在桌面版查看成片与中文说明。素材、参考、文案、风格和时长可写在一句话需求里。每条执行相同的质量标准并保存可恢复进度。对话精修时才在对话里展示中文稿/样片并收集修改。

默认创作追求TikTok带货高表现参考的表现力；具体结构、镜头、字幕字体、贴纸动效、转场和声音包装按内容与客户要求选择。优先使用当前编辑器经核实可用的原生能力。开发样片的视觉参数和个人批准作为原任务记录保存，客户执行约定以项目根AGENTS.md为准。

创作使用与当前需求匹配的素材和参考包，可来自客户提供、已有有效素材库或按需采集，保存来源、用途、表现依据、结构/原话证据和选择理由。先判断是否需要外部来源，再安排采集；TikTok URL 发现须走下文 **Evil 发现硬门**。ASR/OCR 等转录能力若本机未启用，只记录缺口，不得借此跳过 Evil 探针。主任务完整链路使用 `pipeline-inputs`、`run`、`pipeline-evidence`，详见 [完整链路](../../docs/modules/pipeline.md)。Codex提交真实画面观察和语义稿件；工具校验证据后制作，逐条质量复核和用户认可分别记录。

## 素材路线由需求决定

- 客户给出本地素材：先导入、分析和判断可用性；素材齐备后直接进入策划制作。客户明确只用所给素材时遵循该范围。
- 客户希望补充素材，或允许按需要补充：先利用已有素材，只对缺失的商品动作、场景或参考采集，再按创意混剪。
- 客户没有提供素材：按商品、市场与目标风格采集并筛选。客户指定的参考可优先用于结构研究，成片素材用途单独记录。
- 不要求客户额外填写模式表单。Codex从需求判断路线，把依据、路径、补充范围和缺料项写入任务notes；来源变化仍校验实际文件和任务归属。
- 交付耗时按[流程与耗时现状](../../docs/客户交付-流程与耗时现状.md)的口径记录；新任务、缓存复用、客户等待、服务等待和开发排错分开报告。


## Evil 发现硬门（保量 / 桌面补采，必须）

凡需要搜 TikTok 视频页 URL（桌面出片缺料补采、`collect_footage`、对话精修采集），**开搜前必须探针**，禁止未探针就降级仓内线索 / Codex web search / 薄 yt-dlp。

1. **探针**：对 `http://127.0.0.1:8000/readyz`（或 `$VW_EVIL_API_BASE/readyz`）发 GET；同时确认 `docker … ps` 里 **browser-rpc**（常见名 `dtk-browser-rpc-1`）为 healthy。
2. **密钥**：`VW_EVIL_API_KEY` 或 `VW_EVIL_API_KEY_FILE` 已对当前 Codex/ shell 可见（只写路径/环境变量名，**禁止**把密钥正文写进仓库）。
3. **就绪定义（三者同时）**：`/readyz` 为 ok **且** browser-rpc healthy **且** 密钥已导出 → **必须**走 Evil 发现（`workflow/unattended/evil_discovery.py` / `collect_footage` Evil 路径）再交 yt-dlp。
4. **降级（仅此）**：探针失败、browser-rpc 不健康、或缺密钥时，才允许降级 Codex web search → yt-dlp；日志写明降级原因。**禁止**未探针就声称「本机没装 Docker/Evil」。
5. **桌面 Codex 一句话出片缺料**：必须走同一硬门（优先 `scripts`/`collect_footage` Evil 路径），禁止凭仓库旧线索自由发挥薄搜。

保量首装：能装 Docker（Desktop 或 WSL2+Engine）的客户机，Docker+Evil（`--profile browser`）是**保量必装**，不是可跳过可选项；见 [Docker与Evil0ctal自装](../../docs/Docker与Evil0ctal自装.md)。

## 高级入口：workflow.py / 对话精修（非默认）

日常请用上文 **Codex 桌面版出片**。以下仅在客户明确要求精修、调试或逐步确认时使用。不用网页、前端构建或常驻服务器。`<项目目录>` 指本机实际的项目绝对路径（可含中文和空格，使用时加引号）。

macOS / Linux：

```sh
"<项目目录>/.venv/bin/python" "<项目目录>/scripts/workflow.py" capabilities
"<项目目录>/.venv/bin/python" "<项目目录>/scripts/workflow.py" create --title '商品首条' --product '用户指定的商品'
"<项目目录>/.venv/bin/python" "<项目目录>/scripts/workflow.py" create --title '商品巴西批次' --product '用户指定的商品' --market BR --count 7 --style '达人试用'
"<项目目录>/.venv/bin/python" "<项目目录>/scripts/workflow.py" list
```

Windows（PowerShell，部署见 docs/Windows源码部署.md）：

```powershell
& "<项目目录>\.venv\Scripts\python.exe" -X utf8 "<项目目录>\scripts\workflow.py" capabilities
& "<项目目录>\.venv\Scripts\python.exe" -X utf8 "<项目目录>\scripts\workflow.py" create --title "商品首条" --product "用户指定的商品"
& "<项目目录>\.venv\Scripts\python.exe" -X utf8 "<项目目录>\scripts\workflow.py" create --title "商品巴西批次" --product "用户指定的商品" --market BR --count 7 --style "达人试用"
& "<项目目录>\.venv\Scripts\python.exe" -X utf8 "<项目目录>\scripts\workflow.py" list
& "<项目目录>\.venv\Scripts\python.exe" -X utf8 "<项目目录>\scripts\workflow.py" import job_实际ID "D:\素材\商品素材.mp4"
```

`show <job_id>` 读取任务；`import <job_id> <绝对视频路径> [更多明确提供的路径]` 导入素材；`cancel <job_id>` 取消。路径有空格时正常 shell 引号。部署到其他员工电脑时按实际项目位置调整。

`attach-analysis <job_id> <analysis.json 绝对路径>` 核对已生成分析的原片/报告/帧证据后登记到任务，要求源文件哈希对应本任务已导入资产。不会重新分析或代签批准。`show`/MCP get_job 返回 `analyses`，便于换会话接续。采集、LaMa、分析的独立 CLI 分别为 scripts/collect_videos.py、scripts/repair_video.py、scripts/analyze_video.py，读取 docs/modules 中对应命令；LaMa 依赖在隔离环境运行。完整回归使用 `.venv/bin/python scripts/check.py`（Windows：`.venv\Scripts\python.exe scripts\check.py`，或 `scripts\check_windows.bat`）；默认跳过 LaMa 修补测试，需要时加 `--with-repair`。

脚本输出 JSON，并返回 `local_media_path` 与 `local_thumbnail_path`。用 `![素材预览](/绝对路径.mp4)` 在 Codex 对话展示原素材，用缩略图、稿件文件和后续 MP4/SRT 展示每步结果。员工不必手输脚本或操作网页。

已配置 MCP 时可用对应工具，先调用 `workflow_capabilities` 判断已接通阶段，不能把未来能力当作当前已执行。

1. 从员工请求提取商品/SKU、市场、视频数量、预算、负责人、素材或参考链接。支持 MX 墨西哥、BR 巴西、ID 印尼、PH 菲律宾；未指定国家时沿用墨西哥。默认语言分别为 es-MX、pt-BR、id-ID；PH 可选 fil-PH、en-PH、taglish-PH，未说明时创建任务并记录待选，配音前再确定。数量使用本次用户指定的1–100整数；仅说范围时先明确准确目标，同时继续商品与参考调查。示例数字只用于展示调用。
2. （对话精修）创建任务，返回真实任务 ID 和批量进度；按目标数量建立待创作条目。同一任务管理所有序号。交互路径下前三条可为代表样片候选；桌面出片不在对话里逐步确认风格。
3. 根据需求选择素材路线：先对客户明确提供的本地视频路径调用workflow_import_video，查看后判断是否需要补充。新任务已有素材齐备时省略collection，直接提交当前任务的asset_id或sources进入分析制作；已有任务若保存过采集配置而本次只用已有素材，读取后显式提交collection:{}以停用采集；需要且允许补充时，按下文规则采集缺失来源并导入同一任务。没有素材时按需求采集；开搜前执行 **Evil 发现硬门**（就绪则必须 Evil→yt-dlp）。工作流演示录像与商品原素材分别记录。
4. 查询真实元数据/预览/记录并在 Codex 展示。采集入口可处理明确来源的下载、转码和入库；平台 URL 发现走 **Evil 发现硬门**（就绪必须 Evil；仅探针失败/缺密钥才降级 Codex 搜索并保存依据）。
5. 用户新开对话给出任务 ID 时查询后端恢复；不要根据聊天记忆捏造进度。
6. **仅对话精修路径**：使用 `pipeline-inputs <job_id> <输入.json绝对路径>` 登记逐条素材、观察、稿件/审计或已有制作目录；`run <job_id>` 执行。此时中文稿须展示并获明确确认，再用 `pipeline-evidence` 保存批准；内部本地化独立复核后登记同一证据入口，再 `run` 制作交付。**桌面出片默认跳过本步交互确认**；`enqueue` + `watch`/`once --produce` 仅可选调试。缺资料、缺料和失败逐条保留，成功步骤校验哈希复用。TikTok URL 发现已接 Evil（见硬门）；ASR/OCR 仍按本机实际能力查询。第07步发布接入暂停。

员工要求批量视频时，按实际商品素材规划不同的购买顾虑、钩子、证明动作和 CTA，以 `plan-batch <job_id> <concepts.json>` 或 MCP `workflow_plan_batch` 登记具体创意。读取 [批量入口说明](../../docs/modules/batch.md) 的格式与状态语义。目标数量不是素材容量；素材不足保留未完成条目并说明缺什么，不能以换音乐、变速或调顺序凑数量。镜头与稿件模块及输入见 `docs/modules/creation.md`；主链路连接创作、独立审计、准确确认和渲染，技术成片不能代签用户质量认可。

## 创作与渲染阶段继承的规则

### 按需商品广告采集合同

本节适用于当前任务需要采集的来源；客户自带素材足够时直接进入分析制作。

用户要求对标 TikTok 爆款商品带货视频，具有广告风格。优先同款高表现带货内容，再找同类高表现内容研究结构、指定竞品/达人账号及品牌/供应商原片。记录可见播放/互动、发布时间、采集时间和可取得的账号基线；付费投放不是筛选前提。商品匹配、钩子、演示/结果、推广或购买意图必须有依据。结构研究分别记录观察和爆火原因假设，不能把互动当销量、把旧高互动视频当当前趋势。

已验证付费广告与普通商业带货视频分开标注；同款商品素材候选与同类广告结构参考分开。对话精修时可先展示一小批广告候选确认方向；桌面出片按采集合同自动筛选，合格素材不足就失败并报告缺料。配置合同位于 `config/collection-policy.json`，业务规则位于 `docs/采集规则-TikTok商品广告.md`，查找、下载失败或水印处理时读取。平台原生下载、转码和导入已实测；单条 TikTok 干净播放流取得已实测。LaMa 本地 6 秒修补样片已获用户接受，作为默认备用引擎；主链路可提交修补配置，新输出展示对比并完整复核。通用水印识别、录屏和无人值守采集器尚未接通。

桌面出片默认：内部完成镜头计划、中文稿、独立文案审计、本地化与复核并自动放行，不在对话逐步确认。对话精修：先镜头与中文稿审计，中文批准后再本地化；原意/卖点/时长/镜头/可见文字/语音实质变化时用中文重新确认。当地语言全文免用户人工审阅，不将内部审核或自动放行说成用户批准。素材七天禁复用，素材不足等待补料。默认干净 MP4 + 独立 SRT。动作完整、商品证明可信、冻结段检查。

### 口播创作、参考改编与修改

Codex 从客户指定、已有有效参考或按需采集/分析的参考包中选取适合商品与市场的高表现结构，结合真实商品材料创作。员工可以补充“开头更抓人”“这句换掉”“用我给的文案”，也可指定参考覆盖系统选择。口播要有具体购买顾虑、钩子、可见证明与自然购买动作；沿用既有剪辑 skill 的口语质感及独立文案审计。

记录稿件是原创、参考改编还是用户供稿，及修改要求/父版本。复刻参考的钩子、说服顺序、转折、节奏和 CTA，适配商品及市场；保存真实参考文本/结构依据及适配说明。原视频尚无转录时，仅能称为视觉结构参考，不能声称已复刻其原口播。自动 ASR/OCR 的执行状态仍按实际能力查询。

按选定镜头的具体总长和逐句时间窗估计读出时长，品牌名、数字与外文也要考虑。时间轴覆盖全片不等于语音匹配；过长先改稿，过短补具体场景/证明，不拿空镜填时长。中文粗估和最终当地语言口播的时长判断分开；没有真实音频不得声称已实测通过。若后续配音不匹配已批准版本，给出具体调整稿/计划再确认，不擅自加速到听不清、删字或拉长画面。

改稿保留旧版，新版重新审计、重新确认，旧批准不继承。先给代表稿看效果，批量其他稿件按各自创意完成；同样的购买顾虑和开头换几个词不算不同创意。当前独立模块的具体输入和能力以 `docs/modules/creation.md` 为准，不把规则要求当成已经自动完成。

本版是本机执行空间，负责人是记录字段。素材预约在本机 SQLite 排他事务中记录，处理素材映射原片区间，并核对项目内既有 library.sqlite3 台账。多员工身份、跨机器预约及队列待验收；本机交付后由员工完成平台操作。

## 声音硬性规则

**声音硬性规则（用户 2026-10-19）**：任何成片一律不加背景音乐（BGM/配乐/stock music）。无口播 → 成片静音（所有素材 muted，不放任何音频）；有口播 → 只保留口播音轨，不加 BGM。不得调用 search_stock_media 找音乐，不得 push_asset 音乐文件，不得调用 submit_music/submit_sound。
