# 本地成片导出协助

请用本机已连接的 ChatCut Desktop 工具，完成一次短导出。遵守：

1. 参考 `skills/team-video-workflow/chatcut-mcp.md` 与本文件「MCP 参数示例」，不要猜参数。
2. 不要删除任何 ChatCut 工程；不要调用 submit 类工具；不要上传或发布。
   只允许直接调用 chatcut_desktop MCP 工具；**禁止**使用 ChatCut 应用内 AI Agent 对话面板，禁止用 Computer Use/界面点击键入操作 ChatCut。
3. **声音硬性规则**：不加背景音乐（BGM/配乐/stock music）。无口播 → 静音（视频 item 一律 `"muted": true`，不添加任何 audio item）；有口播 → 只保留口播音轨。禁止 search_stock_media 找音乐、禁止 push_asset 音乐文件。
4. 短路径：
   - 用 list_projects 找到名称正好是 TEST-无人值守-20261017 的工程。
   - 用 target_project，参数 projectId 指向该工程。
   - 不要大幅改时间线；必要时只做只读核对。
   - 导出到目录：`{{exports_dir}}`，建议文件名 TEST-unattended-export.mp4。
5. 完成后用 shell（exec_command）写入 UTF-8 文件 `{{result_path}}`（不要用 MCP 工具写本地文件），内容示例：
   {"ok": true, "export_paths": ["/绝对路径/TEST-unattended-export.mp4"], "project_name": "TEST-无人值守-20261017", "notes": "short export", "renderer": "chatcut"}
   若失败则 ok 为 false，并写 error 字段。尽快结束。

## MCP 参数示例（ChatCut Desktop 0.6.13 实测，勿臆造）

code_mode 下调用名为 `tools.mcp__chatcut_desktop__<tool>`，先查 ALL_TOOLS。时间单位：startFrame/durationFrames 为帧；sourceIn 为**整数微秒**。

- duplicate_project（保留原工程）：`{"projectId": "<源UUID>", "name": "xxx v4", "activate": true}` → 返回 newProjectId，随后 `target_project({"projectId": newProjectId})`。
- push_asset：视频 `{"filePath": "/abs/a.mp4", "type": "video"}`；字幕 MG `{"filePath": "/abs/legendas.jsx", "type": "motion-graphic", "width": 1080, "height": 1920, "duration": 17566667, "name": "Legendas"}`（duration 微秒）。
- edit_item（**只有 adds / updates / deletes 三个数组，没有 action、json 参数**；同一调用原子提交）：
  - 新增视频：`{"adds": [{"type": "video", "assetId": "<id>", "trackId": "V1", "startFrame": 0, "durationFrames": 60, "sourceIn": 9400000, "fit": "cover", "muted": true}]}`
  - 新增 MG：`{"adds": [{"type": "motion-graphic", "assetId": "<id>", "trackId": "V2", "startFrame": 0, "durationFrames": 537, "left": 0, "top": 0, "width": 1080, "height": 1920}]}`
  - 修改：`{"updates": [{"id": "<itemId>", "startFrame": 419, "durationFrames": 50}]}`
  - 删除：`{"deletes": [{"id": "<itemId>"}]}`
  - 同轨道 item 不能重叠；先删/缩短再移动，或在同一调用里一起提交。
- edit_track（`json` 是**字符串化**的 JSON）：
  - 列出：`{"action": "list"}`
  - 新建：`{"action": "create", "json": "{\"trackType\":\"video\",\"count\":1}"}`
  - 删除空轨：`{"action": "delete", "trackId": "<轨道id或别名>"}`（先删掉轨道上的 item）
- edit_captions（ChatCut 内置字幕；`action` 必填，其余参数放 `json` 字符串，batch 用 `edits` 数组）：
  - `{"action": "style", "json": "{\"preset\":\"plain\"}"}`
  - `{"action": "batch", "edits": [{"action": "set_card_text", "cardId": "<id>", "text": "Texto"}]}`
  - edit_captions **不能**写本地文件或导出；本项目字幕通常用 MG 层，不必调用。
- read_project：`{}`；preview_timeline：`{"views": ["timeline"], "fromFrame": 0, "toFrame": 537}`。
- export：`{"format": "video", "codec": "h264", "resolution": "1080p", "fps": 30, "outputPath": "/abs/out.mp4"}` → 返回 taskId；再 `track_progress({"action": "wait", "target": "export", "exportTaskIds": "<taskId>"})`。export 只导出，**不能**写其它文件。

## 上下文

- task_id: {{task_id}}
- workflow_job_id: {{workflow_job_id}}
- delivery_dir: {{delivery_dir}}
- exports_dir: {{exports_dir}}
- result_path: {{result_path}}
- request:
```json
{{request_json}}
```
