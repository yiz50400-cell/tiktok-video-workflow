# 配音与剪辑开发模块

## 当前客户交付与创作方向（2026-10-15）

项目面向客户及其团队，以Codex为操作入口，按既定GitHub源码下载与本机部署路线交付。项目默认追求TikTok带货爆款类型的表现力；每条依据客户要求、商品、市场、真实素材和高表现参考设计结构、节奏、字幕字体与包装。具体元素和镜头参数由当前创意确定，样片设置作为候选参考。

执行优先级为本次客户明确要求、该客户已确认的长期偏好、项目通用默认。客户及任务的品牌、声音、字幕和风格选择与开发样片批准分别记录；通用验收检查事实证据、动作完整、叙事、自然口播、完整同步字幕、画面可读性及声音层级。详细约定见[项目规则](../../AGENTS.md)。

原生能力优先用于提高表现力与执行效率，按当前编辑器已核实可用的字幕、字体、贴纸动效、音效和资源选择方案。第2条ChatCut原生字幕实测与参数见制作流程（本机证据，未随仓库：`projects/sky-tubes/batch-mix6/chatcut/native-captions-v1/制作流程.md`），验证范围为该样片。此段记录交付定位与执行规则；客户配置、完整批量流程及跨机器部署的实现和验收，仍按各自真实记录报告。

## 开发验证记录

状态：第 1 条 SHEGLAM Color Bloom Liquid Blush 巴西开发样片 v0002 已按用户确认的新中文稿与六镜头顺序导出，技术检查与抽帧查看通过。用户反馈“剪的不错，还可以”，登记为正面反馈，不扩展为发布或批量验收。本机素材库已接入制作 CLI（2026-10-10，Asia/Shanghai）。

用户认可的是素材采集、去水印与配音的处理方法，不代表固定使用当前素材、中文口播或具体音频。每条内容按商品与创意重新评估素材、中文稿和镜头；中文确认后由内部完成本地化、复核与新配音。市场已认可音色池继续适用，视频间随机选声，单条重试保持选择；音色池认可不等于具体稿件或成片认可。

2026-10-12 用户进一步明确质量标准：配音自然像真人、清楚、无炸麦，认可的音色具备候选资格，每条实际口播另查语速、语气和停顿。画面有头有尾、动作完整、重点明确，跳切和闪帧需检查；每条按这些标准重新选择素材和组织镜头。标题、重点字幕、音效等包装纳入制作方案，并结合采集参考的包装时机和商品动作安排。最新执行约定见项目根 `AGENTS.md`。

本批用户对剪辑画面给予正面反馈，对三条相同音色、慢语速和制作耗时提出修改，并指出标题、音效等包装不足。这些反馈分别记录，不构成整体成片验收。改进目标为批内音色轮换、紧凑自然的语音衔接、参考驱动的包装，以及复用缓存、减少现场写脚本和受控并行；这些目标尚待代码接入和样片验证，本文不将规则更新计为功能完成。

用户反馈保存于 user-review.json（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/user-review.json`）。技术 QA 历史保留，正式成片验收以该用户审核结果为准。

## 当前 v0002

2026-10-12 批次 `2026-10-10-br-03` 已完成三条真人竖屏开发样片：Amy 17.5秒、Rena 20秒、Valeria 16.3秒。中文版本分别为 v0003 / v0002 / v0002，用户中文确认、内部巴葡文字审核与外语免展示证据分开保存。技术检查、PCM音频时间对齐和独立抽帧复核通过，独立听审与用户成片验收待完成。交付和范围见 交付说明（本机证据，未随仓库：`projects/sheglam-color-bloom/batch-run-dev/2026-10-10-br-03/交付说明.md`），当前状态见该目录 `batch-status.json`。本批已登记本机素材及成片使用台账。

本批发现并修复混音末端时间戳造成补静音无法结束的问题：`amix` 后、`apad` 前按 `N/SR/TB` 重建音频时间戳，保持语音采样及镜头计划。新增真实EOF回归覆盖，生产与素材库38项测试通过；独立复核保存为该批次 `code-review.json`。

目录：v0002（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0002/`）。

- 混剪样片：混剪样片.mp4（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0002/混剪样片.mp4`）
- 独立字幕：字幕-pt-BR.srt（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0002/字幕-pt-BR.srt`）
- 批准与新稿：`creative.json`、`zh-approval.json`、`localization-v0001.json`、`localization-audit.json`
- 新配音：`voice.mp3`、`voice-selection.json`、`voice-recording.json`、`word-boundaries.jsonl`
- 执行和检查：`execution.json`、`qa.json`、`visual-review.json`、`code-review.json`

镜头顺序：Isra 涂抹头特写 → Eli 点涂 → Isra 上脸 → Eli 刷具晕染 → Eli 近景结果 → Isra 产品收尾。原始选段为 Isra 30–32.7、40.15–43.25、99.6333–102.3793 秒，Eli 0–1.5、6.85–10.6、10.6–14.05 秒，均一倍速。人物切换可辨；Eli 操作与结果连续，不作跨人物前后证明。

新中文稿由用户 `ok` 确认，巴葡内部独立文本复核通过，外语全文免展示。说明字段有一个 `applicador` 拼写笔误，实际口播原文为正确的 `aplicador`，审核中已记录。随机选声锁定 Francisca，自然语速生成全新录音 15.432 秒；每句通过该录音自身词时间及 -60dBFS 停顿定位，音色池批准与新录音证据分别保存。

Isra 无水印播放源请求返回 HTTP 403，使用既有本地 big-lama 模型只修复三个新增片段。首轮校准掩膜的色点问题有留档；最终使用 `isra-*-mask-v0002.json` 局部矩形掩膜，产物和原始来源 SHA 均绑定。掩膜之外像素在编码前保持不变；首/中/尾修复图及成片 5fps 动作图、切点相邻帧已查看。原有 Eli 产品/色号文字、Isra 顶部 Hot Topic ID 保留，没有新增烧录字幕。

最终 H.264 High level 4.2 / yuv420p / 1080×1920 / 60fps / 1035 帧，视频 17.25 秒，与批准 17.246 秒相差 0.004 秒。帧尾丢失问题经真实输出检查修正：每段先按 60fps 取样再缩放，帧序号生成时间戳；没有静帧补时或加速。AAC 48kHz 单声道，-18.5 LUFS、真峰值 -3.3 dBFS、满幅削波 0；尾句 16.929125 秒结束。完整解码、黑帧、>=0.8秒冻结、孤立亮度闪帧检查通过。独立 SRT 10 个短句。

```bash
.venv/bin/python scripts/produce_video.py render projects/sheglam-color-bloom/production-dev/01-boundary/v0002/execution.json
.venv/bin/python projects/sheglam-color-bloom/production-dev/01-boundary/v0002/verify-sample.py
.venv/bin/python -m pytest -q tests/test_production.py
```

19 项聚焦测试通过，含不同分辨率/帧率来源的实际混剪、画面像素和帧数、新配音稿件/音色/参数/词时间/选择哈希绑定。独立代码复核提出的三项证据绑定问题已修正。CLI 兼容 v0001 单源输入；v0002 使用 `sources` 与每镜头 `source_index`，新录音不要求与旧试听文件同哈希。

本会话没有可听取音频的工具，没有声称新录音人工听审或成片已由用户验收。商用许可、准确SKU/色号、购买路径未核实；未发布、未量产、未调用收费接口。后续修改内容另建编号版本，保持已交付证据可核对。

## 历史 v0001

v0001 用户验收不通过，以下仅保留技术历史，不作为当前交付。先前 `mixed-edit-proposal.md` 已由 v0002 新方案取代。

- 样片：样片.mp4（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/样片.mp4`）
- 独立字幕：字幕-pt-BR.srt（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/字幕-pt-BR.srt`）
- 执行清单：execution.json（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/execution.json`）
- QA 证据：qa.json（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/qa.json`）
- 接触表：contact-sheet.jpg（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/01-boundary/v0001/contact-sheet.jpg`）

## 真实执行

```bash
cd <项目目录>
.venv/bin/python scripts/produce_video.py render \
  projects/sheglam-color-bloom/production-dev/01-boundary/v0001/execution.json
```

本条清单绑定 v0003 中文稿、中文审计/批准、本地化 v0002 与独立复核、外语免展示政策、声音批准及媒体 SHA-256。CLI 验证这些输入字节和视频/音频哈希，分别检查六个镜头与五个口播窗、原速时长、语音切分及字幕；来源变化、越窗或覆盖输入会失败。输出先写临时 MP4，完整解码后替换目标并保存独立 SRT。CLI 是经人工核准清单的执行器，仍需调用方核准清单内容；哈希校验不等于自动审批。

`workflow/production/voices.py` 提供市场限定的随机音色选择及持久化重试验证。已锁定的本条选择保存在 `voice-selection.json`，重试读取 Francisca 参数并验证批准哈希，不重新随机。`workflow/production/qa.py` 提供 PCM 削波、采样峰值、响度与真峰值测量。本地入口直接使用现有音频路径，供应商可以在此输入边界替换；入口不会在线生成或购买音频。当前默认 VideoToolbox 适用于 Mac；`encoder=libx264` 是显式可选项，自动回退与正式团队语音服务尚未实现。

## 验证

```bash
.venv/bin/python -m pytest -q tests/test_production.py
.venv/bin/python projects/sheglam-color-bloom/production-dev/01-boundary/v0001/verify-sample.py
```

本条实测：视频 1080x1920 / 60fps / H.264 High level 4.2 / yuv420p / 1034 帧（17.2333 秒），与批准 17.246 秒相差少于一帧；AAC 解码有封装补齐，容器约 17.25 秒。moov 位于 mdat 前。音频 AAC 48kHz 单声道，-18.8 LUFS、真峰值 -3.3 dBFS、PCM 满幅削波 0；黑帧、>=0.8 秒冻结及孤立亮度闪帧候选无命中。

真实句边界结合与批准试听同哈希的 WordBoundary 数据和 -60 dBFS 停顿检测测得，尾音约 17.118 秒结束，独立 SRT 拆为 8 个短句。第三句的产品名称与动作句、第四句的近景与边缘描述、CTA 问句与下单句按真实词边界拆开。原镜头与口播均未加速、改词或补静帧。10fps 动作取样与相邻切点图已经查看；正常速度人工回放仍待登记。15.116 秒处保留批准源区间中的眼镜/姿态跳切，9.783 秒手部遮镜转场亦保留。

TDD 聚焦测试 14 项覆盖真实临时视频渲染/解码、镜头与口播数量独立、非法时间和越窗、截音、来源哈希与审计绑定、来源文件保护、独立短句字幕、固定声音重试及真实 PCM/真峰值测量。实际 CLI 用本条清单复跑成功，未对共享模块展开回归。

## 尚未完成与边界

目标 SKU/色号、创作者商用复用许可、巴西购买路径仍待核实；未发布、未调用付费服务、未扩展批量成片。葡语全文未向用户展示，也未写成用户亲自审批；本地化仅依据已批准中文稿和独立内部复核。共享 `workflow/api.py`、`cli.py`、`store.py`、`mcp_server.py`、`media.py` 未修改，主会话接入时应读取本模块产物与哈希。

已通过试听的市场音色进入候选池，每条稿件生成对应录音并检查实际听感；本会话无可听取音频的工具，最终成片的独立听感、品牌发音和母语人工声质审查未标记为通过，指标不能代替听感。`qa.json` 明确记录这一限制，用户直接播放本条样片即可评估最终停顿与声质，不要求审阅外语全文。

共享接入建议：主会话读取 execution/qa 文件和 SHA-256，分别登记中文已批准、本地化内部复核通过、外语人工免展示、声音池认可、开发样片技术 QA；最终成片人工验收单独登记。七天素材使用登记已实现为独立本机 SQLite 台账；共享业务库仍由主会话另行接入。本模块没有修改 `.runtime` 或旧 MX 任务。

## 本机素材库与使用台账

用户已批准每位员工以本机素材库复用和管理素材。`workflow/production/library.py` 实现标准库 SQLite 旁路索引；不搬动或复制媒体，不依赖聊天记忆，其他商品可按商品字段登记。员工通过 Codex 输入商品、市场和数量，命令由 Codex 执行。操作约定见 员工使用说明（本机证据，未随仓库：`projects/sheglam-color-bloom/production-dev/local-library/员工使用说明.md`）。

开发库位于 `projects/sheglam-color-bloom/production-dev/local-library/library.sqlite3`。当前真实入库 6 个素材、7 个检查过的片段、2 条成片历史，v0001 为 rejected，v0002 为 positive_feedback。历史使用时间仅精确到日期，以 2026-10-10 Asia/Shanghai 零点作锚点，精度证据在反馈文件及 initialization.json。重复初始化不新增历史、不抹掉审核。

素材按实际 SHA-256 去重，可登记多个路径别名。处理版映射到原片与时间偏移，避重复识别同一原片区间，其他未使用区间仍可选。导入校验原片关系、商品、时长、片段范围并原子提交；内容改变或缺失的文件不进入候选。库内路径相对项目根，完整迁移数据库和媒体后可查询。

`candidates` 默认避开最近七天已用区间，目前此开发库返回 0；原片还有未检查区间，可进一步检查或补充新来源。`check` 报告区间重叠及忽略空格标点的相同中文稿/开头，语义相似需要 Codex 判断；报告不构成自动创意审批或阻止所有重叠。权利状态与视觉检查状态分别保存，当前素材 rights_status 为 unverified。

```bash
.venv/bin/python scripts/produce_video.py library inventory
.venv/bin/python scripts/produce_video.py library history
.venv/bin/python scripts/produce_video.py library candidates --product "SHEGLAM Color Bloom Liquid Blush"
.venv/bin/python scripts/produce_video.py library check projects/sheglam-color-bloom/production-dev/01-boundary/v0002/execution.json projects/sheglam-color-bloom/production-dev/local-library/context-v0002.json
```

`library import MANIFEST` 的 JSON 顶层为 assets 列表，项含 path、sha256、product、creator、duration、review_status、rights_status、clips；clips 项为 start、end、role。处理版还需 original_sha256、original_offset，原片先导入。实际格式及证据见库目录 assets.json。

`render PLAN --workspace ROOT --library DB --context CONTEXT` 自动登记实际成片使用。上下文含 product、market、employee、angle、hook、chinese_lines，格式见 context-v0002.json。相同计划与上下文重试查验输出指纹并跳过编码，仍验证配音、绑定审核文件及时间范围，按计划恢复缺失/改变的独立 SRT。已登记输出改变须另建编号版本。单独 `library record PLAN CONTEXT` 支持现有历史导入；`library review ID STATUS EVIDENCE` 记录真实用户反馈（accepted / rejected / positive_feedback），证据需 user_reply。

库命令均支持 --workspace、--library；inventory/history/candidates 可按 --product 筛选，candidates 可按 --role 与 --cooldown-days 筛选，check 支持 --cooldown-days。每位员工应明确指定本机库路径，当前默认目录只是开发库位置。

验证：`tests/test_production.py` 与 `tests/test_production_library.py` 合计 37 项通过，覆盖真实视频渲染自动登记、跨路径去重、整体迁移、处理片段原片映射、使用重试、文件改变、不同员工独立历史、相同文案识别、真实 CLI 以及缓存配音/审核/SRT 完整性。真实开发库查询证据另存 local-library/verification.json，未重制已交付样片。

此版本未部署其他员工电脑，未接入共享 API/MCP/业务库、采集自动入口或无人值守批量队列。跨员工去重需额外共享台账；已完成的本机库可先通过当前 Codex 入口使用。

## 2026-10-12 Amy 15秒包装版

批次 `2026-10-10-br-03/01-amy/production-v0002` 已按用户“确认”的五句中文、五窗、白字粉色强调与三处轻音效制作。最终1080×1920/60fps/900帧/15秒；源规范视频复用，同人物原速点涂和刷开。Francisca/+15%锁定，实测句间隔0.344/0.260/0.449/0.490秒。中文批准、内部巴葡v0003复核、外语免展示分别保存；没有增加外语人工审批。

技术QA及独立抽帧/切点复核通过，未实际独立试听或正常速度人工观看，最终用户样片方向待验收。桌面副本在 `2026-10-12/Amy双颊试用-v0002/成片.mp4`。另两条本轮未制作修改。

新增 `scripts/prepare_packaged_video.py` 为本条批准五窗准备入口，五句TTS受控并发3、内容/声音/参数及输出哈希缓存，坏缓存只重做对应句，录音参数沿用；`scripts/verify_packaged_video.py` 负责实际MP4技术检查及审片图。可选依赖见 `requirements-production.txt`。`voices.py` 支持显式市场池跨job复用、批内轮换、同义本地化修订的证据绑定与旧选择归档。`produce_video.py` 支持绑定PNG文字层及本地音效；最终包装MP4进入同一台账。`library.existing`拒绝新计划复写已登记输出；准备入口在改资产前保护已有版本，内容改动须另版。

生产/库/包装/缓存相关66项测试通过。实际五句缓存准备0.265秒，最终编码缓存复跑0.289秒且视频SHA不变；该计时只证明此样片缓存续跑，不是全流程稳定产能。完整无人值守编排尚未实现。准确证据：该目录 `execution.json`、`qa.json`、`final-independent-review.json`、`code-review.json`、`cache-verification.json`。

## 2026-10-12 镜头连续性修订

用户评看 Amy `production-v0002` 后指出镜头停留过短、跳切严重，明确拒绝当前画面节奏；已登记用户反馈与本机台账 rejected。复核发现8个剪辑段里还有原源自带硬切，实际19个镜头，最短0.133秒、中位0.933秒，技术闪帧/黑帧检查不能代替观看连贯性。诊断证据见该目录 `continuity-diagnosis.json`。

用户明确选择“换用动作更连续的素材，保持自然速度”。已筛 Isra 连续操作源并制作 `01-amy/continuity-review-v0001/原速镜头预览.mp4`，17.4秒、五镜头2.7/4.5/3.0/4.7/2.5秒、原速；1044帧/60fps/720×1280，完整解码通过。预览保留源水印，无音轨，仅供观看动作与切镜节奏。新第一侧手指轻拍、第二侧刷具晕开，中文候选与变化见 `creative-proposal.json`；尚待用户中文确认，不生成新口播、不将预览计为最终成片。其他两条修改待首条方向认可。

项目根约定新增检查原素材内部真实切点及实际镜头停留；短于2秒须逐项审查，优先连续原速动作。此为素材选择/视觉审核要求，不宣称已实现全自动跳切识别。

## 2026-10-12 首条17.4秒连续动作配音版

用户对准确中文稿及原速预览回复“可以”后，完成 `01-amy/production-v0003`。五段Isra原速片分别2.7/4.5/3/4.7/2.5秒；实际成片1080×1920/60fps/1044帧/17.4秒，四个真实切点2.7/7.2/10.2/14.9秒，未检出源内部额外硬切。原片清理经43个精确帧独立抽查，最终包装再经三张审片图与10个精确帧独立检查；全解码/黑段/冻结/闪帧/音频削波技术检查通过。最终SHA `1aac119373631e47c2645313d036ea16f0948887c017dd91e8caf1b35e74979b`。

中文批准、巴葡v0002内部独立复核和外语免用户全文审阅分别记录。沿用本条Francisca/+15%锁音色，不重新轮换。实测句长1.817/3.994/2.570/3.922/2.217秒，句隙0.913/0.536/0.460/0.808秒；声音自然度未获工具真实听审。白/粉重点文字、点涂/刷开/结果三轻音效，独立SRT。源片576×1024，放大导出不新增细节；修补衣料/挥手区域有轻微软化。

本次素材清理耗时673.89秒，使用本地既有LaMa模型。公共Edge旧合成地址返回403，依据上游PR491在本条隔离准备脚本中改用当前公共合成地址后，五句实际生成成功，没有调用付费接口或变更音色参数。该版本准备入口只对本条数据及工作区根目录验证，已记录跨cwd路径限制；不宣称通用无人值守能力。

后续效率验证已落地：同片 MPS LaMa 296.25 秒；原批准素材/包装复用、五句配音全部重新生成的制作入口总耗时17.740秒，包含准备、渲染及技术QA。窗口、包装、音效及锁定音色保持原批准字节/结构；新录音实际听审与最终验收分别保留待定。175项完整回归通过，限流恢复与完整缓存绑定已接入。详情见 [06效率验证](../06-efficiency-validation-2026-10-12.md) 与 [本地恢复入口](local-batch.md)，不据此扩大为全流程或20条批次产能。

清理后五段分开登记原片SHA与准确original_offset，避免把不连续拼接错误登记成单一连续源；本机台账使用ID `37567e1d1b4c8aa6197f9bcf51c112803534bd781554dc948a2964080b5d0535`。桌面副本 `<本机桌面>/2026-10-12/双颊试用-v0003/成片.mp4` 与项目文件SHA一致。最终用户画面/声音/整体方向验收待定；另两条修改等待首条方向认可。旧版本及拒绝证据保留。
