# 无人值守成片制作（ChatCut Desktop MCP 直连）

用本机已连接的 chatcut_desktop MCP 工具，把已采集的真实 TikTok 素材剪成 **{{count}} 条** {{market}} 市场 TikTok 种草竖屏成片并导出。全程无人值守：不要提问，不要等待确认，遇到工具报错就读报错、按下方示例修正参数后重试。

## 批次硬性规则（count={{count}}）
1. **跨条禁止换序复用**：下方「批次素材分配」给每条成片划定了互不重复的 `clip_ids`。第 N 条只能用自己 bundle 里的素材；**禁止**把同一 `clip_id` / 同源文件在不同条之间换顺序复用凑数。
2. 若某条分配的素材不够用，停下来在结果里 `ok=false` 说明缺料——**不要**去挪用其它条的素材，也不要用换序硬剪。
3. **单条内禁止乒乓剪**：一条时间线里至少用到 bundle 内 ≥2 个不同 `clip_id`；禁止只用 1–2 条源素材来回交替铺满（例如 74→76→74→76… 这种假片节奏）。
4. 导出 **恰好 {{count}} 个** 成片到 `export_paths_json` 列出的路径（count=1 时只用 `export_path`）。

## 规则
1. 参考 `skills/team-video-workflow/chatcut-mcp.md` 与本文件「MCP 参数示例」，不要猜参数；不确定时先看 ALL_TOOLS 里的 inputSchema。
2. 不要删除、改写任何已有 ChatCut 工程；只在本次**新建**的工程里编辑。不要调用 submit_* / delegate_task / import_url / search_stock_media；不要上传或发布。
   只允许直接调用 chatcut_desktop MCP 工具；**禁止**使用 ChatCut 应用内 AI Agent 对话面板，禁止用 Computer Use/界面点击键入操作 ChatCut。
3. **声音硬性规则**：不加背景音乐（BGM/配乐/stock music）。本任务没有口播 → 成片静音（视频 item 一律 `"muted": true`，不添加任何 audio item）。禁止 search_stock_media 找音乐、禁止 push_asset 音乐文件。
4. 只能使用下方「素材清单」里的文件（已校验的真实 TikTok 素材），不得使用其它视频、库存素材或生成素材。
5. 本地文件（字幕 MG 的 .jsx、结果 JSON）一律用 shell（exec_command）写入 `{{work_dir}}`；MCP 工具不能写本地文件。

## 步骤
0. **先连通 ChatCut**：chatcut_desktop 工具在 code mode 里（ALL_TOOLS 中的 `mcp__chatcut_desktop__*`），首轮工具列表看不到是正常的。第一件事就是执行 `await tools.mcp__chatcut_desktop__list_projects({})`；成功后照常调用其它工具（例如 `await tools.mcp__chatcut_desktop__create_project({...})`）。不要在没试过的情况下判定工具不可用。
1. **看素材（已过初筛）**：素材清单里每条都已通过「同款形态 / 烧录文字 / 水印 / 生活方式」视觉初筛，`usable_segments` 是**唯一允许使用**的源片段（秒）。可用 view_image 查看 `screen_grid`（4×3 宫格，按行从左到右对应 `screen_times`）确认画面。
   - 每个镜头的 `[sourceIn, sourceIn+时长]` 必须**完整落在某个 usable_segment 内**；片段外的画面一律不用（那些位置有烧录字幕、水印、看不到商品或是纯生活方式镜头）。
   - **开头第 1 个镜头和最后的 CTA 镜头**必须来自 `product_prominent: true` 的片段（`prominent_times` 附近），商品清楚、是画面主体；不许用人物/风景等生活方式镜头收尾。
   - `needs_segment_lama` / `deferred_burn_salvage`: true 的素材仍可按 usable_segments 上时间线，但**导出前必须跑步骤 7.5** 仅对用到的窗口做 LaMa；未修就导出 = AC-8 失败。
   - 片段带 `"crop": "bottom"` / `"crop": "top"` 表示该片段原片有烧录文字，**必须裁掉**才能用：该镜头 item 不要写 `fit`，改用放大 1.35 倍的手动位置把文字推出画布——crop=bottom：`"left": -189, "top": 0, "width": 1458, "height": 2592`；crop=top：`"left": -189, "top": -672, "width": 1458, "height": 2592`（其余字段照常，`muted: true`）。`"crop": null` 的片段照常用 `fit: "cover"`。导出后会逐镜头自动复核，残留原片文字会被判不合格并要求重剪。
   - 可用片段较短时，用更短的镜头（≥1.0 秒）或在同一片段内取不同起点，不要越界。
2. **分镜（每条成片各自一份）**：8–10 个镜头，每个 1.0–2.5 秒（30–75 帧 @30fps），总时长 15–22 秒；**本条**至少用到自己 bundle 内 2 个不同 `clip_id`，且不要整条时间线只在两段源之间乒乓。结构：钩子（痛点/惊艳结果）→ 商品出场 → 2–4 个功能/效果演示 → 便利性/卖点 → 结尾 CTA。
3. **建工程**：每条成片一个工程（或 duplicate 后再改）。`create_project({"name": "{{project_name}}"（count>1 时加 " #1"/" #2"…）, "compositionWidth": 1080, "compositionHeight": 1920, "fps": 30})` → `target_project` → `edit_track({"action": "list"})`；需要时再建视频轨，保证有 V1（素材）和 V2（字幕 MG，在上层）。
4. **导入素材**：对选用的素材 `push_asset({"filePath": "<clip path>", "type": "video"})`，记录 assetId。
5. **上时间线**：在 V1 上按分镜一次（或分几次）`edit_item` adds，镜头首尾相接、不重叠、无空隙，`fit: "cover"`、`muted: true`，`sourceIn` 为**整数微秒**。
6. **字幕 MG**：参照示例 `{{example_mg_path}}`（React/Remotion 风格：useCurrentFrame/interpolate/AbsoluteFill 已全局可用，不要 import），用 shell 写 `{{work_dir}}/captions.jsx`：
   - 语言：{{caption_language}}（{{caption_locale}}），口语化 TikTok 种草语气；每个镜头一句（≤ 8 个词），CUTS 与 V1 镜头起始帧一致，最后一个值 = 总帧数。
   - 顶部字幕（top≈250）、白字黑描边、半透明黑底；钩子句用黄色加大；最后一个镜头加结尾 CTA 胶囊按钮（纯文字，例如 {{cta_example}}）。
   - emoji 只用这些可渲染的：😩 🙌 ✨ 🔥 ✅ 💪 🔋 ⚡ 😍 👀 💯（**不要用 🛒**，渲染不出来）。
   - `push_asset({"filePath": "{{work_dir}}/captions.jsx", "type": "motion-graphic", "width": 1080, "height": 1920, "duration": <总帧数/30 秒换算成整数微秒>, "name": "captions"})`；若校验失败按返回诊断修改 jsx 后重新 push。
   - 在 V2 上 adds 一个 motion-graphic item：`startFrame 0`、`durationFrames = 总帧数`、`left 0, top 0, width 1080, height 1920`。
7. **核对**：`read_project({})` 确认 V1 镜头连续、MG 覆盖全片、**没有任何 audio 轨/item**；`preview_timeline` 抽看 2–3 个时间点画面与字幕。有问题就修。
7.5. **仅用段 LaMa（有 `needs_segment_lama` / `deferred_burn_salvage` 的素材必须做；否则跳过）**：
   - 采集阶段默认**不**整段修烧录字（避免 8G CPU 卡死）。成片仍须满足 AC-8：最终画面无源烧录字。
   - 从当前 V1（或即将写入结果的 `shots` / `footage_used`）整理用到的窗口，用 shell 写 `{{work_dir}}/used_shots.json`：
     `{"shots":[{"clip_id":"...","path":"<源 path>","source_in_s":3.4,"duration_s":2.0,"index":0}, ...]}`
     每个镜头 1.0–8.0 秒（典型 3–8s）；`path`/`clip_id` 必须对应素材清单。
   - 跑（只要有 deferred 素材）：
     `python -m workflow.unattended.burn_repair --used-segments {{work_dir}}/used_shots.json --clips {{segment_lama_clips_path}} --workdir {{segment_lama_workdir}} --ffmpeg {{ffmpeg}}`
   - 读 stdout JSON 的 `replacements[]`：对每条 `push_asset({"filePath": repaired_path, "type":"video"})`，再用 `edit_item` **updates** 把对应 V1 镜头换成新 `assetId`，并设 `sourceIn: 0`（整数微秒）、`durationFrames` 保持原镜头时长。
   - **禁止**为求快改用 boxblur / delogo / 灰色糊条；LaMa 失败就修参数重试或换其它可用 KEEP 镜头，不要降级糊字。
   - 再 `preview_timeline` 抽查：源烧录字应消失；MG 字幕保留。
8. **导出**：对每条成片 `export` 到 `export_paths_json` 中对应路径（count=1 时即 `{{export_path}}`）→ `track_progress` wait。
9. **写结果**：用 shell 写 UTF-8 JSON 到 `{{result_path}}`。
   - count=1 时保持旧格式：
     {"ok": true, "renderer": "chatcut", "project_id": "<uuid>", "project_name": "{{project_name}}", "export_paths": ["{{export_path}}"],
      "duration_frames": <总帧数>, "captions_file": "{{work_dir}}/captions.jsx", "caption_lines": ["..."],
      "shots": [{"index": 0, "path": "<clip path>", "source_in_s": 3.4, "duration_s": 2.0, "start_frame": 0, "duration_frames": 60, "crop": null, "shows": "画面内容"}],
      "footage_used": [{"path": "<clip path>", "source_url": "<url>", "clip_id": "<id>", "source_in_s": 3.4, "duration_s": 2.0, "shows": "画面内容"}],
      "footage_rejected": [], "notes": "..."}
   - count>1 时必须写：
     {"ok": true, "renderer": "chatcut", "project_name": "{{project_name}}",
      "export_paths": <export_paths_json 原样>,
      "videos": [
        {"video_index": 0, "project_id": "<uuid>", "export_path": "<path1>", "shots": [...], "footage_used": [{"path":"...","clip_id":"..."}], "caption_lines": ["..."]},
        {"video_index": 1, "project_id": "<uuid>", "export_path": "<path2>", "shots": [...], "footage_used": [...], "caption_lines": ["..."]}
      ],
      "notes": "跨条 clip_id 互斥；单条无乒乓剪"}
   失败时 ok=false 并写 error。最终回复同样输出这个 JSON。

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
- market: {{market}}
- work_dir: {{work_dir}}
- export_path: {{export_path}}
- export_paths_json:
```json
{{export_paths_json}}
```
- count: {{count}}
- result_path: {{result_path}}
- request:
```json
{{request_json}}
```

## 批次素材分配（硬性；每条只用自己的 clip_ids）
```json
{{batch_assignments_json}}
```

## 商品形态定义
{{product_definition}}

## 素材清单（全池，已过采集校验 + 视觉初筛；剪辑时仍以「批次素材分配」为准）
```json
{{footage_json}}
```
