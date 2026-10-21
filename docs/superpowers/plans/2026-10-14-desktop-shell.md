# Desktop Shell Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task in the current authorized session.

**Goal:** 接通真实 Codex 的中文桌面业务界面并生成可分发的 Windows 测试安装构建。

**Architecture:** Electron 主进程负责凭据、stdio RPC、后端及发行资源。React 只通过固定 preload API 操作现有 FastAPI 链路。Python 提供准确中文审阅和交付预览，现有制作不改业务批准。

**Tech Stack:** Electron 44.7.2、electron-builder 26.15.3、React 19、Python 3.12、Codex CLI 0.152.0。

**Spec:** docs/superpowers/specs/2026-10-14-desktop-shell-design.md

## Global Constraints

- Key 不返回 renderer、不写日志或发行包；safeStorage 不可用不明文保存。
- 只确认准确中文版本，不代签当地语言/声音/最终质量。
- 固定版本与发行白名单；Windows 未实机不能宣称正式安装包已验收。
- 无 Git，现有原型保留；新界面使用 desktop-ui 与 desktop-dist。

### Task 1: 中文稿和交付 API

Files: workflow/desktop/reviews.py、routes.py、scripts/desktop_server.py、tests/test_desktop_api.py。

Interfaces: GET /api/jobs/{id}/desktop/reviews；POST /api/jobs/{id}/desktop/confirm {ordinal,creative_sha256}；GET /api/jobs/{id}/desktop/delivery/{ordinal}/video。

- [x] 创建真实临时 job/creative，断言中文预览包含稿件、时间和包装；提交旧哈希必须拒绝且没有批准文件。
  ```python
  response = client.post(url, json={'ordinal':1,'creative_sha256':'0'*64})
  assert response.status_code == 409
  ```
- [x] pytest tests/test_desktop_api.py -q 观察缺接口失败。
- [x] 读取任务绑定路径生成中文视图；确认在任务锁内对当前哈希验证并调用原 save_evidence；交付视频仅当前任务 manifest 验证后提供。
- [x] 测试成功确认/重复确认/变更后拒绝/非本任务资源拒绝。

### Task 2: 桌面运行时与 Codex

Files: desktop/rpc.cjs、settings.cjs、codex.cjs、main.cjs、preload.cjs；desktop-tests/*.test.cjs；scripts/desktop_config.py。

Interfaces: Rpc.request(method,params)、notify(method,params)、respond(id,result)、close()；Settings.public()/save()/read()；Codex send(jobId,text)/interrupt()/decision(id,accept)。

- [x] 编写真子进程 JSONL 交错/分包/断线测试及加密 store/线程映射恢复测试。
  ```javascript
  assert.equal(await rpc.request('probe', {value: 7}), 7);
  assert(!JSON.stringify(store.public()).includes(secret));
  ```
- [x] node --test desktop-tests/*.test.cjs 观察缺接口失败。
- [x] 实现 RPC 超时及清理，safeStorage 注入式 store，独立 CODEX_HOME、线程绑定和白名单 UI 通知；主进程固定 IPC、后端随机端口和生命周期。
- [x] 用真实 Codex 0.152.0 app-server 执行一次短回答和本地读文件，记录状态/耗时，不保存原始协议凭据。

### Task 3: 中文员工界面

Files: desktop-ui/{main.tsx,App.tsx,bridge.ts,styles.css}、desktop.html、vite.desktop.config.ts。

- [x] 接入四市场任务创建、对话、设置导入/保存/联调、进度、审阅按钮及预览。失败提示保留任务上下文，制作成功数与质量认可分开。
- [x] npm run desktop:ui；执行浏览器实测创建任务、输入数量、设置表单及错误状态，截图复核布局。
- [x] 完整 Python 与 Node 回归，确认历史入口仍通过。

### Task 4: 固定 Windows 运行资源和安装构建

Files: scripts/stage_windows_runtime.py、desktop/builder.yml、distribution/licenses、docs/Windows桌面交付-2026-10-14.md。

- [x] 白名单 staging 测试拒绝个人配置/历史数据；包内 Python/Windows wheel/Codex/FFmpeg 有版本与哈希清单。
  ```python
  assert not (stage/'engine'/'projects').exists()
  assert not (stage/'engine'/'.runtime').exists()
  ```
- [x] 下载固定 Windows Python standalone、安装平台 wheel、提取固定 Codex Windows 分发；白名单引擎复制。
- [x] 执行 electron-builder Windows x64 NSIS；记录实际产物、哈希、构建结果及平台限制。
- [x] 保存中文员工说明和 Windows 安装/更新/卸载/恢复/正式视频验收清单，测试构建与正式可交付分别记录。
