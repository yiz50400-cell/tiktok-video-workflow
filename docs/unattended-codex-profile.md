# ChatCut 瘦身 Profile（桌面出片 + 可选调试）

## 用途

客户日常默认是 **Codex 桌面版**出片；本 profile 给桌面 / 可选调试路径提供**只含 ChatCut Desktop MCP** 的瘦身配置，避免全量 MCP 把单次输入顶到约 16 万 token。

**不是**「必须天天跑 `codex exec`」的说明。`codex exec` / `once --produce` / `watch` 仅**可选调试**。

## 一次性安装

### macOS / Linux

```bash
cp config/unattended-chatcut.config.toml "${CODEX_HOME:-$HOME/.codex}/unattended-chatcut.config.toml"
```

按本机路径改 `chatcut-mcp`（或设 `CHATCUT_MCP`）。保持 `CHATCUT_MCP_CLIENT = "codex_app"`、`code_mode_host = true`、`computer_use = false`。

### Windows

1. 复制 `config/unattended-chatcut.windows.example.toml` → `%USERPROFILE%\.codex\unattended-chatcut.config.toml`（不要用 macOS 的 `Library/Application Support/...` 路径；若设了 `CODEX_HOME` 则用该目录）。
2. 把 `command` 改成该机 `chatcut-mcp` 绝对路径，或设置环境变量 `CHATCUT_MCP`。
3. 保持 `CHATCUT_MCP_CLIENT = "codex_app"`、`code_mode_host = true`、`computer_use = false`。
4. Windows 无自动重启：制作前手动打开 ChatCut Desktop。

## 可选调试：`codex exec`（非默认）

仅工程师排障 / 金丝雀 / `once --produce` 路径需要。须已装 Codex CLI（与桌面同账号）。确认：`codex exec --help` 中 `-p NAME` 会在用户配置之上叠加 `$CODEX_HOME/<NAME>.config.toml`。

```bash
codex exec -m gpt-5.6-sol -p unattended-chatcut \
  -s danger-full-access -c approval_policy='"never"' \
  -c model_reasoning_effort='"high"' --skip-git-repo-check \
  -C "/绝对路径/视频工作流" \
  -o /tmp/codex-last.txt \
  "按 skills/team-video-workflow/chatcut-mcp.md 完成导出…"
```

可选调试 worker（`workflow/unattended/producer.py`）在 CLI `--produce` 时仍使用上述参数；代码保留，**客户默认请走 Codex 桌面版**。

也可用多次 `-c` 覆盖单项，但不推荐替代本 profile。

## 注意

- `-p` 默认叠在用户 `config.toml` 之上；调试 worker 会加 `--ignore-user-config`，只加载本 profile 的 `chatcut_desktop`，避免 Slack/其它 MCP 鉴权噪音与超大 token。
- 若报 `503 … 模型 gpt-5.6-sol（或可选 gpt-5.6-astra） 无可用渠道`，是上游 distributor 问题，与 ChatCut/收件箱无关；可换可用 `--codex-model`。

## Provider 注意

本机使用自定义 `model_provider`（如 foropencode）时，**不要**加 `--ignore-user-config`，否则会落到默认 openai 渠道。`-p unattended-chatcut` 叠加载 ChatCut MCP 即可。

## ChatCut 沙箱

制作必须能访问 ChatCut 的本地 `mcp.sock`。`workspace-write` 会被拒绝（Operation not permitted），调试 worker 因此使用 `-s danger-full-access`，同时保持 `approval_policy=never`。日志会脱敏 bearer token。
