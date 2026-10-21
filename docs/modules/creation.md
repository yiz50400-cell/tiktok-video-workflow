# 镜头时间计划与中文稿模块

状态：独立 CLI 已完成；SHEGLAM Color Bloom Liquid Blush 的 Eli 干净版有 MX 内部稿 v2 和 BR/pt-BR 目标的中文内部稿 v2。MX v1 独立文案审计为需修改、v2 通过；BR v2 商业吸引力五项审计通过，**中文稿尚未得到用户批准**。本模块未接入共享任务库，未本地化、配音、剪辑或发布。更新：2026-10-09（Asia/Shanghai）。

## 独立运行

依赖项目现有 `.venv` 的 `imageio-ffmpeg` 及已有分析模块；不调用网络、付费 LLM 或 TTS。CLI 由 Codex 或员工给出已经观察过的计划与中文稿，本地只验证结构和保存版本，不自动生成文案。

```bash
cd <项目目录>
.venv/bin/python scripts/plan_video.py create \
  --spec projects/sheglam-color-bloom/creation-dev/eli-v2-input.json \
  --output projects/sheglam-color-bloom/creation-dev/eli
.venv/bin/python scripts/plan_video.py audit \
  --version projects/sheglam-color-bloom/creation-dev/eli/v0002.json \
  --input projects/sheglam-color-bloom/creation-dev/eli-v2-audit-input.json
.venv/bin/python scripts/plan_video.py show \
  --version projects/sheglam-color-bloom/creation-dev/eli/v0002.json
```

上面 `create`/`audit` 是生成时用过的命令；对应 v2 与审计文件已经存在，**不可对同一输出重跑并覆盖**。制作新稿时，另写新输入 JSON，`create` 自动生成下一个 `vNNNN.json`；审计写入对应的 `vNNNN-audit.json`。文件以排他创建方式保存，版本记录 `parent_version` 与 `parent_sha256`，读取和续写会校验版本链。审计绑定准确版本文件 SHA-256，不能由撰稿者自审，也不能写入人类批准。`show` 合并呈现版本和审计的有效状态；新版本始终重新置为待审/待用户批准，不继承旧审计和批准。

## 素材与证据

多来源链路从当前任务关联的 `Asset.metadata_json.original_sha256` 解析规范原片身份；字段缺失时使用 `Asset.sha256`。同一原片的 raw/repaired 或两个 repaired 在输入登记和起稿时均拒绝作为多个来源，起稿缓存复用前也会重查。新的多来源稿保留每个来源的 `original_sha256`，分析和编辑文件的实际 SHA 字段继续绑定各自文件。原片血缘进入多来源与修补稿的缓存指纹；普通旧单源缓存及既有稿件、审计和批准文件按原字节保留。

Python `create_version(spec, output_dir, *, trusted_source_originals=...)` 接收由可信调用方从任务素材注册表解析的 `{编辑文件SHA: 规范原片SHA}`，要求精确绑定当前所有编辑文件。spec JSON 中的 `original_sha256` 或 `trusted_source_originals` 会被拒绝；CLI 不把 JSON 字段转换为可信参数。独立调用未提供注册表上下文时只验证分析/编辑文件哈希，其多来源 `source_diversity.lineage_check` 明确为 `file_sha256_only`；兼容字段 `used_unique_originals` 在这个边界下表示按分析文件哈希去重的来源数量，不能单独作为真实多原片验收。带可信上下文的多来源或修补稿记录 `task_asset_metadata`。任意文件的重新编码关系无法仅由不同 SHA 自动识别；导入衍生素材须先保存准确的原片血缘。

- 分析源：Eli 播放版（本机证据，未随仓库：`projects/sheglam-color-bloom/watermark-demo/eli-player.mp4`），SHA-256 `abb6098af41cb8b27f55bee6f346458979114dbfa914d5c9f93dab1e5b7be901`，18.13 秒，HEVC。
- 编辑用：Eli 兼容干净版（本机证据，未随仓库：`projects/sheglam-color-bloom/watermark-demo/eli-clean.mp4`），SHA-256 `4fe0ee7bdc95ead5f85bd2f27735f134560cb53c6a86d405c52e27e863c4977f`，18.14 秒，H.264。它是同一播放源转码后的文件；关系由 verification.json（本机证据，未随仓库：`projects/sheglam-color-bloom/watermark-demo/verification.json`） 的原文件名、URL、兼容版哈希、时长及完整解码结果核对，不能用编辑版哈希冒充分析源哈希。
- 证据入口：Eli analysis.json（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/eli/analysis.json`） 及其 manifest、observations、真实取帧和报告。计划逐镜头引用已观察区间和区间内帧 ID；ASR/OCR 均为 `not_run`，分析语义状态仍为 `codex_observed_needs_review`。区间在 4 fps 回放取样后核准，精确渲染切点仍需在剪辑阶段复核一帧以内的边界。
- Isra 现有原片有平台水印，且未锁定同一 SKU，本条计划只使用 Eli；不把不同人物的妆效拼成同人前后对比。

## 首条计划

完整 计划和稿件 v2（本机证据，未随仓库：`projects/sheglam-color-bloom/creation-dev/eli/v0002.json`）、可读报告（本机证据，未随仓库：`projects/sheglam-color-bloom/creation-dev/eli/report.md`）、文案审计（本机证据，未随仓库：`projects/sheglam-color-bloom/creation-dev/eli/v0002-audit.json`）。7 段相加为 14.54 秒；5.67–9.03 秒保留同侧脸颊的再次点涂与连续刷具晕染。开头彩色灯光仅用作视觉钩子，不作准确肤色、色号或受控前后对比证据。

中文首稿（v2，逐字待用户批准）：

> 刚点上脸，先别慌。\
> 这支 Color Bloom 液体腮红，先点在脸颊。\
> 拿刷子，沿着脸颊慢慢晕开。\
> 转过来，再近一点看，脸颊这边已经晕开了。\
> 想试先确认色号，再下单。

v2 独立文案审计检查口语自然度、画面事实、时长适配与 CTA 真实性四项，结论 `passed_needs_user_approval`。这是模型复核，不是人工审批。末句 2.10 秒最紧，后续获批逐字稿的真实配音必须核对完整读出和停顿；若改字、时长或可见/可听内容，创建新版本并重新确认。

## 校验与限制

`create` 先检查分析索引、manifest、观察、报告、原视频、证据页和取帧的 SHA-256，再检查编辑视频的实际元数据、转码关系、每个镜头源区间、证据区间和帧 ID；成片总长由镜头时长求和，显式总长若不一致则拒绝。中文稿按成片时间排布，拒绝重叠、明显空白和提前结束。所有新稿均按公开估算器逐行核对时长：CJK 每秒 5.0 字、拉丁词 0.42 秒、数字词 0.35 秒、标点停顿 0.12 秒，估算/分配时长比值需在 0.65–1.12。明显超速或过短会报 `runtime estimate`；估算是中文内部筛选，不能替代最终本地化音频测量。审计中声称 `runtime_fit=true` 必须通过重新估算，包括结论为需修改的审计。历史审计文件不被改写，旧审计若声称通过但新的估算失败，`show` 会返回 `needs_revision_runtime_warning`。

新稿保存 `script_source`：`original_from_visual_evidence`（原创视觉证据稿）、`collected_reference`（上游系统收集参考）、`adapted_from_reference`（参考改编）、`user_supplied`（用户供稿）或 `revision`（修订）。未指定时默认为原创视觉证据稿；系统采集与用户供稿分别记录 `acquisition`，用户文字文件保存路径及哈希。参考来源保存路径/URL/来源名、文本可用状态、改编说明和 `hook`、`buyer_concern`、`proof`、`objection_or_turn`、`cta` 五个结构节拍。没有原文时范围只能是 `visual_structure_only`，不能声称复刻原口播；修订稿保存实际父版本和修订请求。结构校验不能替代实际配音的语速和内容审计。

```bash
.venv/bin/python -m pytest -q tests/test_creation.py
```

本模块单测覆盖真实可解码临时媒体、镜头和稿件时段、证据引用、转码双哈希、旧版保留、审计独立性、旧批准不继承及版本链篡改。项目主任务 `job_2e217982bbdd` 当前仍是 `ANALYSIS_REVIEW`；共享 CLI/API/store/MCP 与 `.runtime` 库由主会话接入。建议主会话以 `vNNNN.json` 路径和 SHA-256 及审计路径/哈希登记计划资产，状态维持创作待用户确认，不代签分析或稿件批准。

2026-10-10 验证：creation 聚焦测试 22 项通过；`.venv/bin/python scripts/check.py` 统一回归 88 项通过，另有 Starlette/AnyIO 的第三方弃用警告 1 条。

待配置：具体色号/SKU、账号实际商品卡或购买链接路径、创作者商业复用许可。中文稿获用户批准后才能本地化；最终目标市场逐字口播获用户批准后才能配音或渲染。

## 多市场与批量创作契约

多市场字段沿用共享 `workflow.markets.market_profile()`：`market` 支持 `MX`、`BR`、`ID`、`PH`，`voiceover_locale` 分别为 `es-MX`、`pt-BR`、`id-ID`；PH 可选 `fil-PH`、`en-PH`、`taglish-PH`，未选时保持 `language_status=pending_selection`。单条具体版本同时保存 `market_profile`、目标 `voiceover_locale`、`script_language=zh-CN`（内部审核稿）和 `voiceover_timing_status=pending_localized_voiceover`。镜头总长是编辑计划，不是当地最终口播时长；本地化后的真实读速必须重新测量。

单条市场稿还要保存 `creative_angle`：`angle`、`hook`、`buyer_concern`、`proof_action`、`cta_direction`、`source_asset_ids` 和七天复用政策。市场版本的审计在原四项（口语自然度、证据准确、时长适配、CTA 真实性）上增加 `commercial_attractiveness`；旧的无市场历史版本仍接受四项审计，新的市场稿缺少商业吸引力字段会被拒绝。审计通过只表示模型复核通过，`human_approval.status` 仍为 `pending_user`。

`scripts/plan_video.py batch-contract --spec <json> --output <json>` 提供 creation 侧批量计划契约，输入字段与共享概念登记兼容：`ordinal`、`angle`、`hook`、`buyer_concern`、`proof_requirement`、`cta_direction`、`source_asset_ids`。输出明确保存 `target_count`、`planned_count`、`rendered_count=0`、前三条 `representative_count`、待规划序号和每条的 `material_status`/`proof_support_status`。没有素材 ID 的条目是 `awaiting_asset_binding`，不构成证明；批量模板不会填满 24 条。

契约会拒绝标准化文本重复、仅以换音乐/变速/调顺序为差异、非可用资产、七天内已用资产，以及未经明确授权的同资产多条复用。共享入口只登记待审创意；creation 仍要为实际条目生成时间计划、中文内部稿和独立审计。示例见 巴西具体计划（本机证据，未随仓库：`projects/sheglam-color-bloom/creation-dev/br/report.md`）、巴西批量契约（本机证据，未随仓库：`projects/sheglam-color-bloom/creation-dev/br-batch-contract.json`）：BR/pt-BR，24 个目标槽位，3 条已规划概念，0 条成片，第 4–24 条待规划。

2026-10-10 多市场扩展验证：creation 单测 22 项通过；巴西 v2 具体计划 CLI 创建、商业五项审计和批量契约 CLI 均成功；全量 `scripts/check.py` 88 项通过，另有 1 条第三方弃用警告。未调用付费服务、配音、渲染或发布基础设施。
