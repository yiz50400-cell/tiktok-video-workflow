# ChatCut MCP 操作要点

## 开工前先做健康检查

- 先运行 scripts/chatcut_health.py（默认 dry-run），以 list_projects 这类只读轻量 MCP 调用在默认 10 秒内返回作为健康标准。
- ChatCut 进程存在但 MCP 超时仍判定为不健康；macOS 可在人工确认后显式加 --apply 重启。无人值守主路径不要调用任何 submit_* 工具。
- 自动重启目前只在 macOS 实现；Windows 上会明确报未实现，请手动保持/重启 ChatCut。Windows 部署用 `config/unattended-chatcut.windows.example.toml`，不要照抄 macOS 的 Library 路径。

## 声音硬性规则

- **声音硬性规则（用户 2026-10-19）**：任何成片一律不加背景音乐（BGM/配乐/stock music）。无口播 → 成片静音（所有素材 muted，不放任何音频）；有口播 → 只保留口播音轨，不加 BGM。不得调用 search_stock_media 找音乐，不得 push_asset 音乐文件，不得调用 submit_music/submit_sound。

## 参数与调用顺序

- list_projects：用于连通性和项目清单读取，不把“进程存在”当作 MCP 健康。
- duplicate_project：先确认源项目与新副本名称，保留原项目；禁止删除项目，禁止覆盖同名用户项目。
- target_project：后续编辑和导出前明确切到目标副本，避免沿用陈旧 active project。
- edit_item / edit_track / edit_captions / export 的已验证参数示例见 `workflow/unattended/prompts/chatcut_produce.md`（ChatCut 0.6.13 实测）。edit_item 只接受 adds/updates/deletes 数组，没有 action/json 参数；edit_captions 用 action + json（batch 用 edits 数组）。

- export：先 target_project，再用当前工具返回的真实参数 schema；本地导出不耗 credits，但必须保持 ChatCut 登录且在线。导出后用 ffprobe 做独立校验。
- 若首轮 MCP 工具未加载，先重新建立 chatcut-only MCP 会话/profile，再重试只读探针；不要直接进入编辑。

## Codex profile

为 ChatCut 制作准备独立瘦身 profile，只加载 chatcut_desktop 与完成项目选择、字幕编辑、导出和必要本地校验所需的工具。避免每轮加载全部 MCP，降低输入 token 和首轮工具缺失风险。
