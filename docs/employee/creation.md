# 中文稿与制作方案

> **无人值守默认跳过对话审稿**：后台内部生成并自动放行。下文的「用户只审中文稿」仅适用于客户明确要求的对话精修路径。

读取当前任务的分析 manifest 和实际观察。通过 pipeline-inputs 设置 spec_path 和 audit_path，run 生成绑定版本的中文审核方案。

spec 包含 job_id、product_series、author、market、voiceover_locale、angle、hook、buyer_concern、proof_action、cta_direction、edit_video_path、shots、script_lines、production_config。

shots 每项含 role、source_start_seconds、source_end_seconds、description、evidence_segment_index、evidence_frame_ids；script_lines 含 start_seconds、end_seconds、text 及可选 visible_text_zh。

混剪时将单个edit_video_path换为sources列表，各项包含edit_video_path（独立调用创作模块还须提供analysis_path）；与链路item.sources逐项对应。每个shot必须明确source_index（从0开始），其时间、观察区间和帧ID均按对应来源核验。min_unique_sources=2要求至少两个实际使用的不同原片。保存版本的sources记录全部来源证据；顶层analysis/edit_video是第一个来源的兼容索引，不能代表全部素材。

独立文案审计含 reviewer、verdict、checks（spoken_naturalness、evidence_accuracy、runtime_fit、cta_truthfulness、commercial_attractiveness）、notes。审计人员不能等于作者。

对话精修路径：用户只审中文稿和镜头、包装、声音方案。批准绑定 creative SHA；禁止用开发授权代替中文确认。本地化保持原意时内部独立复核后继续，不新增外语人工审批；实质变更中文重新确认。无人值守路径不逐步征求这些确认。
