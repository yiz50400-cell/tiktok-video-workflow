# 商品任务到本机交付

员工向 Codex 说明商品、市场和本次数量。Codex准备以下输入并调用工具，员工查看中文稿、样片和结果。当前数量为1–100整数；数量仅为范围时先明确准确目标，同时调查商品与参考。

## 素材入口与客户要求

先读取客户要求及已提供素材，按实际需要选择自带素材制作、补充采集或混合使用。客户只允许自带素材时按该范围执行；素材足够时可直接进入后续阶段。商品素材与风格参考分别标注用途。

已实现的本地入口为workflow.py import：导入客户明确提供的路径，取得当前任务asset_id。随后在pipeline-inputs中填写asset_id，或为混剪填写sources；collection是可选配置，已有素材齐备时可省略。已有任务保存过collection且本次改为只用已有素材时，先读取任务并通过pipeline-inputs显式提交collection:{}，再run；仅省略本次字段会保留之前的配置。空对象会让主链路跳过采集。

客户要求、选定素材路线、允许补充的范围和缺料项由Codex记录。默认创意及质量标准见项目根AGENTS.md；耗时与待验证项见[流程与耗时现状](../客户交付-流程与耗时现状.md)。

## 主入口

```sh
.venv/bin/python scripts/workflow.py pipeline-inputs job_实际ID /绝对路径/链路输入.json
.venv/bin/python scripts/workflow.py run job_实际ID
.venv/bin/python scripts/workflow.py show job_实际ID
.venv/bin/python scripts/workflow.py pipeline-evidence job_实际ID /绝对路径/准确证据.json
```

对应 API 为 `/api/jobs/{job_id}/pipeline/inputs`、`pipeline/run`、`pipeline/evidence`；MCP为 `workflow_pipeline_inputs`、`workflow_run_pipeline`、`workflow_pipeline_evidence`。已有本机访问令牌机制沿用。CLI使用同一服务逻辑，无需常驻服务；MCP需要本机API运行。

## 输入与执行

输入包含 `items`、可选 `collection` 和 `workers`（1或2）。每条以 `ordinal` 定位，范围为1至任务数量。提交某条替换该条配置，其余序号保留。路径均为已存在的绝对路径。

| 输入/证据 | 执行与缺项时的待办 |
| --- | --- |
| collection：brand、product、seeds、urls、local_sources、download、resolve_urls、max_candidates | 明确来源的采集、转码和下载；校验原片后导入任务。TikTok URL 发现走本机 Evil（就绪必须 Evil；探针失败/缺密钥才降级 Codex 搜索） |
| item.asset_id | 校验素材属于当前任务，运行分析并返回联系图和manifest |
| item.repair（可选） | mask_spec、model、start、duration、device；隔离调用LaMa并保存task和对比画面。新输出等待视觉复核 |
| repair.review_path | 当前task和修补输出哈希，reviewer，status=passed_internal_visual_review；full_playback、product_action_preserved、outside_mask_review均为true。通过后入库并保留原片血缘 |
| item.observations_path | Codex真实画面观察，按analysis模块校验后登记任务；缺项返回WAITING_OBSERVATIONS |
| item.spec_path / audit_path | creation模块的中文稿、镜头、参考依据和独立审计；缺项返回WAITING_DRAFT / WAITING_COPY_AUDIT |
| spec.production_config | 已确定的市场音色池及SHA、语速参数、画面尺寸/编码、中文可见文字、包装样式、音效/音乐和连续动作复核。中文审核文件展示时段、镜头、文字和声音方案 |
| chinese_confirmation | 用户查看准确中文稿后，绑定creative SHA并保存真实user_reply、source_turn_id；开发授权不能代替稿件确认 |
| localization | 保存准确当地语言稿、内部独立审核及哈希；实质变化先中文重新确认，保持原意时免展示外语全文 |
| READY_PRODUCTION | 按序号锁定音色；制作前预留原片区间，再受控并行执行单条准备、渲染、技术检查和交付 |

新生成的creative包含 `production`。镜头与时长从已审计的creation版本派生；稿件及包装逐句对应真实时间窗，句数不写死。配置驱动单条准备支持单源或多源。`packaging`的每项含line、font绝对路径、font_sha256、font_size、x、y及可选color；`sound_effects`含path、sha256、start、duration、volume和供中文确认的description_zh。成片不加背景音乐：无口播则静音，有口播则只保留口播（用户 2026-10-19 硬性规则）；`sound_effects` 不得用于放置音乐。

`source_continuity_review`须为 `passed_codex_source_review`，有reviewer；shots逐项记录index（从1开始）、source_sha256、source_start/end、internal_cuts及action_complete=true。每个短于2秒的实际镜头须在short_shot_reviews记录range与reason。源片内部切点加入成片切点检查图。这些字段是实际播放复核记录，工具只校验绑定和覆盖范围，不能自动证明动作完整。

证据入口body为 `{ordinal,kind,records}`。中文确认kind为 `chinese_confirmation`。本地化kind为 `localization`，records含localization、audit及可选version。审核需绑定creative、中文批准、当地稿件、市场/语言及独立reviewer。保存的当地稿件为UTF-8、ensure_ascii=False、indent=2、末尾换行；内部审核SHA须对应准确字节。

已有制作目录可用 `production_directory`，须保留原任务ID/序号且所有制作素材已导入该任务；`adopt_verified:true`仅核验已有制作证据并复制交付，不生成新音频或成片，不能计为新产能。

## 进度、恢复与版本

`pipeline.target_count`为任务目标，`drafted_count`为已保存稿件/制作目录数，`delivered_count`为技术核验并交付数。当前 `rendered_count`与delivered_count同步，失败的渲染或QA不计入该成片计数。`batch.planned_count`仍表示明确登记的创意数；有稿件不自动代替创意登记或用户确认。逐条状态和next_action保存在主任务，文件位于 `.runtime/pipelines/{job_id}`。

同任务排他锁阻止重复执行。声音先按序预留，单条重试沿用音色及参数。阶段成功后校验输出哈希复用；失败只重试该阶段，已完成条目校验后复用。损坏交付包、输入变化及已有不匹配证据拒绝覆盖，应采用新版本目录。改变语言清除旧派生进度和旧制作引用；取消优先显示取消状态，并在制作前和交付前检查。运行中修改语言或创意会被锁拒绝。

本机素材预约使用SQLite排他事务，七天内重叠的原片区间拒绝再次使用。同任务、序号和creative版本的重试幂等，失败预约保留以供恢复。修补素材映射原片及时间偏移；同时读取项目内 `projects/**/library.sqlite3` 的血缘和使用记录。并发预约测试验证一个冲突区间只有一个成功者。其他处理入口导入的变体须先登记准确血缘到既有素材台账；字节SHA本身不能判断两个视频来自同一原片。跨机器预约和多员工身份未实现。

## 本机交付与质量

原子生成每条交付包：成片.mp4、字幕-{locale}.srt、中文口播.txt、交付说明.md、internal证据和manifest。重复调用核对全部哈希；包损坏不会静默覆盖。中文稿用户批准、当地语言内部通过、外语全文免用户审阅分别记录。

技术检查覆盖解码、时长、尺寸、音量、字幕及文件绑定；真实听感、完整播放、动作连续、文字遮挡和声音层级仍需逐条复核。用户认可按实际反馈记录。其他市场先试听批准对应音色池，巴西批准不能直接继承。

Codex继续负责语义步骤；自动搜索、ASR/OCR及通用水印识别待接通。第三方配音可用性和限流仍可能导致等待，成功音频缓存保留。平台上传和发布由员工自行完成。
