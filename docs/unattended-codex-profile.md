# 无人值守 Codex 瘦身 Profile

## 背景

全量 MCP 时单次 `codex exec` 输入可达约 16 万 token。无人值守制作只需要 ChatCut Desktop MCP。

## 安装

```bash
cp config/unattended-chatcut.config.toml "${CODEX_HOME:-$HOME/.codex}/unattended-chatcut.config.toml"
```

`codex exec --help` 确认：`-p NAME` 会在用户配置之上叠加 `$CODEX_HOME/<NAME>.config.toml`。

也可用多次 `-c` 覆盖单项，但不推荐替代本 profile。

## 推荐调用（模型固定 6a）

```bash
codex exec -m gpt-5.6-sol -p unattended-chatcut \
  -s danger-full-access -c approval_policy='"never"' \
  -c model_reasoning_effort='"high"' --skip-git-repo-check \
  -C "/绝对路径/视频工作流" \
  -o /tmp/codex-last.txt \
  "按 skills/team-video-workflow/chatcut-mcp.md 完成导出…"
```

无人值守 worker（`workflow/unattended/producer.py`）默认使用上述参数；CLI `--produce` 时由 Supervisor 调用。

## Windows

1. 复制 `config/unattended-chatcut.windows.example.toml` → `%USERPROFILE%\.codex\unattended-chatcut.config.toml`（不要用 macOS 的 `Library/Application Support/...` 路径）。
2. 把 `command` 改成该机 `chatcut-mcp` 绝对路径，或设置环境变量 `CHATCUT_MCP`。
3. 保持 `CHATCUT_MCP_CLIENT = "codex_app"`、`code_mode_host = true`、`computer_use = false`。
4. Windows 无自动重启：制作前手动打开 ChatCut Desktop。


## 注意

- `-p` 默认叠在用户 `config.toml` 之上；无人值守 worker 会加 `--ignore-user-config`，只加载本 profile 的 `chatcut_desktop`，避免 Slack/其它 MCP 鉴权噪音与超大 token。
- 若报 `503 … 模型 gpt-5.6-sol（或可选 gpt-5.6-astra） 无可用渠道`，是上游 distributor 问题，与 ChatCut/收件箱无关；可换可用 `--codex-model`。

## Provider 注意

本机使用自定义 `model_provider`（如 foropencode）时，**不要**加 `--ignore-user-config`，否则会落到默认 openai 渠道。`-p unattended-chatcut` 叠加载 ChatCut MCP 即可。

## ChatCut 沙箱

制作必须能访问 ChatCut 的本地 `mcp.sock`。`workspace-write` 会被拒绝（Operation not permitted），worker 因此使用 `-s danger-full-access`，同时保持 `approval_policy=never`。日志会脱敏 bearer token。
