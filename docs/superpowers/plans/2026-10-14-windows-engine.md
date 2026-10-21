# Windows Engine Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task in the current authorized session.

**Goal:** 交付 Windows 引擎适配、中转联调入口及可重复的实机验收入口。

**Architecture:** 跨平台基础模块承担锁、原子发布、子进程生命周期和依赖定位。中转使用独立 Codex 配置目录和环境凭据。现有业务批准和制作配置保持证据约束。

**Tech Stack:** Python 3.12+、fcntl/msvcrt、Windows Job Object、FFmpeg/libx264、Codex CLI 0.152.0。

**Spec:** docs/superpowers/specs/2026-10-14-windows-engine-design.md

## Global Constraints

- 不修改个人中转配置、历史批准、音色和发布权限。
- Key 不进入日志、报告、命令行或发行包。
- Mac 验证不能计为 Windows 实机验收。
- 当前目录无 Git；直接保存文件，不创建提交。

### Task 1: 锁与文件发布

Files: workflow/platforms/locks.py、workflow/platforms/files.py；修改 orchestration/pipeline.py、repair/pipeline.py、production/voices.py、orchestration/local.py、scripts/run_local_batch.py。

Interfaces: acquire(stream, blocking=True)、release(stream)；atomic_json(path, value, replace=True)。

- [x] 测试先失败：子进程持锁时第二进程非阻塞申请被拒绝；异常释放后能再次获得；关闭临时句柄后才能替换，旧记录在失败后保留。
  ```python
  with file.open('a+b') as stream:
      acquire(stream, blocking=False)
      release(stream)
  ```
- [x] 运行 `.venv/bin/python -m pytest tests/test_platform_runtime.py -q`，观察缺失接口失败。
- [x] 实现 POSIX flock / Windows 单字节锁；将所有现有锁接入，原子发布关闭文件后执行 replace/link。
- [x] 执行平台测试及音色/链路相关回归。

### Task 2: 子进程、编码及运行环境

Files: workflow/platforms/windows_job.py、workflow/platforms/runtime.py；修改 workflow/orchestration/process.py、制作/检查脚本、UTF-8 业务文件读写；新增 scripts/start.bat。

Interfaces: run(command, cwd, timeout, env=None, input=None)；find_uv()；默认 libx264。

- [x] 测试先失败：中文输出在非 UTF-8 终端仍正确；超时清理孙进程；Windows Job 入口失败时不得启动业务进程；中文空格目录真实编码解码。
  ```python
  result = run([sys.executable, '-c', "print('中文')"], cwd=tmp_path, timeout=10)
  assert result.stdout.strip() == '中文'
  ```
- [x] 运行 tests/test_pipeline_process.py 和平台测试，记录失败。
- [x] Windows 包装进程创建 kill-on-close Job 并将自身加入，再启动媒体子进程；POSIX 进程组保持 TERM/KILL。输入输出明确 UTF-8，启动器定位 uv.exe 与 .venv/Scripts/python.exe。
- [x] 执行进程测试和真实 FFmpeg 诊断；缺失历史字体明确拒绝，配置字体仍须精确哈希。

### Task 3: 隔离中转与验收入口

Files: workflow/codex/relay.py、scripts/check_relay.py、scripts/check_windows_engine.py、tests/test_codex_relay.py；README 和验证报告。

Interfaces: load_relay(config_path)；check_relay(codex, config_path, timeout=90) → 脱敏 dict；引擎诊断返回平台及真实编码/解码结果。

- [x] 测试先失败：从选中的 provider 读取 Key/env_key；隔离配置无 Key；子进程通过环境收到凭据；错误事件含 Key 也不能出现在报告；原个人配置字节不变。
  ```python
  relay = load_relay(config_path)
  assert relay.public()['model'] == 'gpt-6-sol'
  assert secret not in repr(relay)
  ```
- [x] 运行 tests/test_codex_relay.py，观察接口缺失失败。
- [x] 实现临时 CODEX_HOME、env_key、ephemeral Codex exec、限时与事件摘要；只存脱敏状态和耗时。新增 Windows 源码启动及平台验收脚本。
- [x] 现有中转真实短回答和本地工具联调；运行完整 scripts/check.py；保存实测结果，Windows 未执行的测试明确待实机。

## 后续发行门禁

Windows 11 干净机器真实执行本计划测试和新任务制作，之后制作安装 EXE，内置固定 Python/FFmpeg/Codex，凭据由每位员工本机配置；安装、更新、恢复、卸载需独立验收。

- [ ] Windows 11 实机观察 Job Object、Windows 文件锁与启动入口测试结果；当前仅保存平台门禁，不标为通过。

## 执行记录

源码及本机验证完成。Windows 专属测试未执行，不计为通过；详见 docs/Windows引擎与中转验证-2026-10-14.md。Task 2 的 Windows 观察步骤由 Windows 实机门禁继续执行。当前完整回归含 3 条 Windows 专属测试跳过。
