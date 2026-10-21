# 当前素材分析

使用当前任务已导入的素材。调用 workflow.py pipeline-inputs 绑定 asset_id，run 生成实际取帧与 manifest；查看真实画面后保存 observations_path，再 run 登记。

观察文件绑定 source_sha256、observer、observed_at。segments 包含 kind、evidence_status、start_seconds、end_seconds、description、frame_ids；known_facts、popularity_hypotheses、unknowns、risks 分别记录事实、假设、缺项和风险。

源片内部切点及关键动作必须回放检查。ASR/OCR 未执行时保持未执行状态。禁止继承其他任务观察、许可或批准。
