"""ChatCut + Codex produce worker for unattended supervisor (phase-2 MVP)."""

from __future__ import annotations

import hashlib
import json
import re
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .inbox import Inbox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_TEMPLATE = Path(__file__).resolve().parent / "prompts" / "chatcut_produce.md"
CANARY_PROMPT_TEMPLATE = Path(__file__).resolve().parent / "prompts" / "chatcut_canary.md"
EXAMPLE_MG_PATH = Path(__file__).resolve().parent / "prompts" / "examples" / "captions_mg_example.jsx"
MARKET_LABEL = {"MX": "墨西哥", "BR": "巴西", "ID": "印尼", "PH": "菲律宾"}
CTA_EXAMPLE = {"MX": "¡CÓMPRALO YA!", "BR": "GARANTA A SUA NO CARRINHO!", "ID": "CHECKOUT SEKARANG!", "PH": "ADD TO CART NA!"}

from workflow.unattended.codex_mcp_gate import verify_codex_chatcut_mcp_ready
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
CODEX_MODEL_ENV = "VW_CODEX_MODEL"


class CodexModel:
    @staticmethod
    def resolve(explicit: str | None = None) -> str:
        """Model for every codex exec call: explicit (--codex-model) > env VW_CODEX_MODEL > default."""
        if explicit and explicit.strip():
            return explicit.strip()
        env = os.environ.get(CODEX_MODEL_ENV, "").strip()
        return env or DEFAULT_CODEX_MODEL

DEFAULT_TIMEOUT_SECONDS = 900.0
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
# Delivery resume: a finished mp4 that already landed in delivery/ must not trigger a full re-cut
# just because ledger/state write failed afterward (Windows e2e: ~3h wasted on re-cuts).
MIN_DELIVERY_BYTES = 300_000
# Review issues that mean the *footage* is bad — re-editing the same clips cannot fix them.
NON_RETRYABLE_REVIEW_MARKERS = (
    "不是同款商品",
    "同款商品",
    "原片烧录文字",
    "烧录文字过多",
    "错品",
)


def qualifying_delivery_videos(delivery_dir: Path, *, min_bytes: int = MIN_DELIVERY_BYTES) -> list[Path]:
    """Return delivery mp4/mov files large enough to count as a finished cut."""
    if not delivery_dir.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(delivery_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            if path.stat().st_size >= min_bytes:
                found.append(path)
        except OSError:
            continue
    return found


def review_issues_need_new_footage(issues: list[str]) -> bool:
    """True when auto-review failed for wrong-product / source burned text — do not ChatCut-retry."""
    blob = "；".join(str(i) for i in issues)
    return any(marker in blob for marker in NON_RETRYABLE_REVIEW_MARKERS)


def _default_chatcut_agent_log() -> Path:
    from workflow.chatcut.health import default_chatcut_agent_log

    return default_chatcut_agent_log()


DEFAULT_CHATCUT_AGENT_LOG = _default_chatcut_agent_log()

# Prepended to every produce prompt (including custom templates).
FORBIDDEN_INAPP_AGENT_RULES = """## 硬性禁止（违反即判制作失败）
- **禁止**使用 ChatCut 应用内的 AI Agent / 对话面板（“今天想创作什么？”、“告诉 AI 要做哪些修改”输入框），禁止往其中输入或粘贴任何提示词；它会消耗 ChatCut 账户额度。
- **禁止**用 Computer Use / cua / 点击、键入、AppleScript 等界面自动化操作 ChatCut 窗口。
- 只能调用 chatcut_desktop MCP 工具直接剪辑：create_project / target_project / push_asset / manage_timelines / edit_track / edit_item / edit_captions / preview_timeline / export / track_progress 等。
- **禁止**调用任何 submit_* 生成类工具（submit_image/submit_video/submit_voice/submit_music/submit_sound/submit_avatar_video/submit_lipsync）。
- **禁止背景音乐**：不加 BGM/配乐/stock music，不调用 search_stock_media 找音乐，不 push_asset 任何音乐文件。无口播 → 成片静音（视频素材 muted:true，不加任何音频 item）；有口播 → 只保留口播音轨。
- chatcut_desktop 工具通过 code mode 暴露（ALL_TOOLS 里的 `mcp__chatcut_desktop__*`，调用写法 `await tools.mcp__chatcut_desktop__list_projects({})`），首轮工具列表里看不到是正常的。**必须先实际调用一次 list_projects**；只有该调用报错/不存在时，才在结果文件写 {"ok":false,"error":"chatcut_desktop MCP tools unavailable"} 并结束，不要寻找替代路径（不要改用 Computer Use 或应用内 AI）。

"""

_SECRET_RE = re.compile(
    r'(?i)((?:experimental_bearer_token|api_key|access_token|authorization)\s*[=:]\s*)(["\']?)([^\s"\']+)(\2)'
)


def _redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(r'\1\2***\4', text)


def _as_text(value: Any, limit: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = _redact_secrets(text)
    if limit is not None and len(text) > limit:
        return text[-limit:]
    return text




def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _health_ok(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    if hasattr(result, "healthy"):
        return bool(result.healthy)
    if isinstance(result, dict):
        if "healthy" in result:
            return bool(result["healthy"])
        status = result.get("status")
        if isinstance(status, str):
            return status.lower() in {"healthy", "ok", "ready", "available"}
    status = getattr(result, "status", None)
    if isinstance(status, str):
        return status.lower() in {"healthy", "ok", "ready", "available"}
    return False


def default_probe() -> Any:
    from workflow.chatcut.health import probe_chatcut

    return probe_chatcut()


def render_produce_prompt(
    *,
    request: dict[str, Any],
    task_id: str,
    workflow_job_id: str,
    exports_dir: Path,
    delivery_dir: Path,
    result_path: Path,
    template_path: Path | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    template = (template_path or DEFAULT_PROMPT_TEMPLATE).read_text(encoding="utf-8")
    template = FORBIDDEN_INAPP_AGENT_RULES + template
    merged = {
        "count": str((request or {}).get("count") or 1),
        "batch_assignments_json": "[]",
        "export_paths_json": "[]",
    }
    merged.update(extra or {})
    for key, value in merged.items():
        template = template.replace("{{" + key + "}}", str(value))
    template = template.replace("{{market}}", str(request.get("market") or ""))
    return (
        template.replace("{{task_id}}", task_id)
        .replace("{{workflow_job_id}}", workflow_job_id)
        .replace("{{exports_dir}}", str(exports_dir))
        .replace("{{delivery_dir}}", str(delivery_dir))
        .replace("{{result_path}}", str(result_path))
        .replace("{{request_json}}", json.dumps(request, ensure_ascii=False, indent=2))
    )


def _parse_log_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_inapp_agent_runs(log_path: Path | None, since: datetime) -> list[dict[str, Any]]:
    """Return ChatCut in-app AI Agent runs logged at/after ``since``.

    ChatCut writes one JSON object per line to logs/agent.log. An in-app agent turn
    shows as "Agent SDK attempt started" and ends with an "Execution completed" line
    carrying ``cost``. Any such run during unattended produce is a policy violation.
    """
    if not log_path or not Path(log_path).is_file():
        return []
    runs: dict[str, dict[str, Any]] = {}
    costs: list[dict[str, Any]] = []
    with Path(log_path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            when = _parse_log_time(entry.get("time"))
            if when is None or when < since:
                continue
            msg = entry.get("msg")
            if msg == "Agent SDK attempt started":
                rid = str(entry.get("requestId") or len(runs))
                runs[rid] = {
                    "request_id": rid,
                    "project_id": entry.get("projectId"),
                    "started_at": entry.get("time"),
                    "cost": None,
                    "duration_ms": None,
                }
            elif msg == "Execution completed" and "cost" in entry:
                costs.append(entry)
    for entry in costs:
        rid = str(entry.get("requestId"))
        run = runs.get(rid)
        if run is None:
            run = runs.setdefault(rid, {"request_id": rid, "project_id": None, "started_at": entry.get("time")})
        run["cost"] = entry.get("cost")
        run["duration_ms"] = entry.get("durationMs")
    return list(runs.values())


def build_codex_command(
    *,
    codex_bin: str,
    project_root: Path,
    last_message_path: Path,
    model: str = DEFAULT_CODEX_MODEL,
    profile: str = "unattended-chatcut",
    use_profile: bool = True,
    extra_writable_dirs: list[Path] | None = None,
    json_events: bool = False,
) -> list[str]:
    command = [codex_bin, "exec", "-m", model]
    if use_profile:
        # Layer slim ChatCut MCP on the user config (keeps custom model_provider).
        # Do NOT use --ignore-user-config: that drops custom provider and breaks foropencode.
        command.extend(["-p", profile])
    command.extend(
        [
            "-s",
            "danger-full-access",
            "-c",
            'approval_policy="never"',
            "-c",
            'model_reasoning_effort="high"',
            # gpt-5.6-sol defers MCP; without code_mode_host tools never reach the model.
            "--enable",
            "code_mode_host",
            "--disable",
            "computer_use",
            "--skip-git-repo-check",
            "-C",
            str(project_root),
            "-o",
            str(last_message_path),
            "--color",
            "never",
        ]
    )
    for directory in extra_writable_dirs or []:
        command.extend(["--add-dir", str(directory)])
    if json_events:
        command.append("--json")
    command.append("-")
    return command


def default_codex_runner(
    prompt: str,
    *,
    cwd: Path,
    log_path: Path,
    last_message_path: Path,
    timeout: float,
    codex_bin: str = "codex",
    model: str = DEFAULT_CODEX_MODEL,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    extra_writable_dirs: list[Path] | None = None,
) -> subprocess.CompletedProcess[str]:
    root = project_root or PROJECT_ROOT
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    use_profile = (codex_home / "unattended-chatcut.config.toml").is_file()
    command = build_codex_command(
        codex_bin=codex_bin,
        project_root=root,
        last_message_path=last_message_path,
        model=model,
        use_profile=use_profile,
        extra_writable_dirs=extra_writable_dirs,
        json_events=True,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd),
            env=merged_env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"codex exec 超时（{timeout}s）",
                    "command": command,
                    "model": model,
                    "stdout": _as_text(exc.stdout, 8000),
                    "stderr": _as_text(exc.stderr, 8000),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
    (log_path.parent / "codex-events.jsonl").write_text(_redact_secrets(completed.stdout or ""), encoding="utf-8")
    # ok means delivery-complete, not raw process exit. Rewritten True only after
    # qualifying files land in delivery/ (see Producer._mark_produce_log_delivery_ok).
    payload = {
        "ok": False,
        "process_exit_ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "model": model,
        "duration_seconds": round(time.time() - started, 3),
        "stdout_tail": _as_text(completed.stdout, 12000),
        "stderr_tail": _as_text(completed.stderr, 12000),
        "note": "ok=true only after qualifying delivery; process_exit_ok is raw codex exit",
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return completed


@dataclass
class ProduceOptions:
    unattended_auto_approve: bool = True
    allow_ffmpeg_fallback: bool = False
    codex_bin: str = "codex"
    codex_model: str = field(default_factory=CodexModel.resolve)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    project_root: Path = PROJECT_ROOT
    forbid_inapp_agent: bool = True
    chatcut_agent_log: Path | None = DEFAULT_CHATCUT_AGENT_LOG
    # "production": collect real TikTok footage -> MCP edit -> verify export.
    # "canary": short export of the TEST-无人值守 project (first-deploy smoke test).
    mode: str = "production"
    discover_timeout_seconds: float = 480.0
    discover_attempts: int = 2
    discover_rounds: int = 2
    discover_max_searches: int = 5
    seed_dir: Path | None = None
    # Post-export auto-review: re-edit at most this many times before giving up (never deliver a failing cut).
    # Keep low: each retry is a full Codex+ChatCut pass (Windows e2e saw ~29min wasted in 4 retries).
    review_retries: int = 1
    reviewer: Callable[..., Any] | None = None
    min_clips: int = 3
    collector: Callable[..., Any] | None = None


@dataclass
class ProduceResult:
    status: str
    reason: str
    delivery_paths: list[str]
    renderer: str | None = None
    codex_returncode: int | None = None
    audit_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "delivery_paths": self.delivery_paths,
            "renderer": self.renderer,
            "codex_returncode": self.codex_returncode,
            "audit_path": self.audit_path,
        }


class Producer:
    def __init__(
        self,
        inbox: Inbox,
        *,
        probe: Callable[[], Any] | None = None,
        codex_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        options: ProduceOptions | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.inbox = inbox
        self.probe = probe or default_probe
        self.codex_runner = codex_runner or default_codex_runner
        self.options = options or ProduceOptions()
        self._log = log or (lambda message: print(f"[无人值守制作] {message}"))

    def produce(self, *, task_id: str, workflow_job_id: str, request: dict[str, Any]) -> ProduceResult:
        task_dir = self.inbox.task_dir(task_id)
        exports_dir = task_dir / "exports"
        delivery_dir = task_dir / "delivery"
        audit_dir = task_dir / "audit"
        logs_dir = task_dir / "logs"
        for path in (exports_dir, delivery_dir, audit_dir, logs_dir):
            path.mkdir(parents=True, exist_ok=True)

        try:
            health = self.probe()
        except Exception as exc:
            return self._fail(
                task_id,
                workflow_job_id,
                f"ChatCut 健康探针异常，未启动制作：{exc}",
                delivery_paths=[],
            )
        if not _health_ok(health):
            return self._fail(
                task_id,
                workflow_job_id,
                "ChatCut 不健康，未启动制作；请先 scripts/chatcut_health.py（必要时 --apply）",
                delivery_paths=[],
            )
        self._log("ChatCut 健康探针通过，开始制作。")

        gate = verify_codex_chatcut_mcp_ready()
        if not gate.ok:
            return self._fail(
                task_id,
                workflow_job_id,
                f"Codex chatcut_desktop MCP 门禁失败，未启动制作：{gate.reason}",
                delivery_paths=[],
            )
        self._log(f"Codex MCP 门禁通过：{gate.reason}")

        audit_path = None
        if self.options.unattended_auto_approve:
            audit_path = self._write_auto_approve_audit(audit_dir, task_id, workflow_job_id, request)
            self._log(f"已写入无人值守自动放行审计：{audit_path}")

        result_path = task_dir / "produce-result.json"
        last_message_path = task_dir / "codex-last-message.txt"
        log_path = logs_dir / "codex-produce.json"
        stages: dict[str, Any] = {}
        self._stages = stages
        production = self.options.mode == "production"
        clips: list[dict[str, Any]] = []
        extra: dict[str, str] | None = None
        self._batch_count = 1
        self._batch_allowed_ids: list[set[str]] = []
        self._export_paths_planned: list[Path] = []
        template_path = None if production else CANARY_PROMPT_TEMPLATE

        # P0-1: if delivery already has a qualifying mp4, treat as success — never re-cut.
        existing_delivery = qualifying_delivery_videos(delivery_dir)
        if existing_delivery:
            delivered = [str(p) for p in existing_delivery]
            prior_clips = self._load_collection_clips(task_dir)
            reason = (
                f"断点续跑：delivery 已有合格成片 {len(delivered)} 个，跳过重剪"
                f"（{', '.join(Path(p).name for p in delivered)}）"
            )
            stages["resume_delivery"] = {"ok": True, "delivery_paths": delivered, "skipped_recut": True}
            self._update_state(
                task_id, workflow_job_id, status="produced", reason=reason,
                delivery_paths=delivered, renderer="chatcut_resumed",
                codex_returncode=None, audit_path=audit_path,
            )
            self._log(reason)
            if prior_clips:
                self._record_used_footage(request, prior_clips, task_id)
            return ProduceResult(
                status="produced", reason=reason, delivery_paths=delivered,
                renderer="chatcut_resumed", audit_path=str(audit_path) if audit_path else None,
            )

        if production:
            collection = None
            reused_manifest = self._load_ok_collection(task_dir)
            if reused_manifest is not None:
                collection = reused_manifest
                stages["collect"] = {
                    "ok": True, "seconds": 0.0, "resumed": True,
                    "reason": collection.reason, "manifest": collection.manifest_path,
                    "clips": len(collection.clips), "candidates": len(collection.candidates),
                }
                self._log(f"断点续跑：复用已有采集结果（{len(collection.clips)} 条），跳过采集。")
            else:
                collect_started = time.time()
                self._log("采集阶段开始：Codex web 检索 TikTok 候选 → yt-dlp 下载 → 校验。")
                try:
                    collection = self._run_collector(request, task_dir)
                except Exception as exc:
                    stages["collect"] = {"ok": False, "seconds": round(time.time() - collect_started, 1), "error": str(exc)}
                    return self._fail(task_id, workflow_job_id, f"采集阶段异常：{exc}", delivery_paths=[], audit_path=audit_path)
                stages["collect"] = {
                    "ok": collection.ok, "seconds": round(time.time() - collect_started, 1),
                    "timings": collection.timings, "reason": collection.reason,
                    "manifest": collection.manifest_path, "clips": len(collection.clips),
                    "candidates": len(collection.candidates),
                }
                if not collection.ok:
                    from .batch_footage import unique_clips as _unique_clips
                    if collection.clips and _unique_clips(list(collection.clips)):
                        self._log(
                            f"采集未达满额但有可用素材，继续尝试部分交付："
                            f"{collection.reason}"
                        )
                        stages["collect"]["partial"] = True
                    else:
                        return self._fail(task_id, workflow_job_id, f"采集阶段失败：{collection.reason}",
                                          delivery_paths=[], audit_path=audit_path)
                self._log(collection.reason)
            from dataclasses import asdict as _asdict
            from .batch_footage import (
                collect_targets,
                partition_clips_disjoint,
                video_count,
            )
            clips = [_asdict(c) for c in collection.clips]
            requested_n = video_count(request)
            batch_n = requested_n
            self._batch_requested_count = requested_n
            self._batch_partial_note = ""
            partition = partition_clips_disjoint(clips, batch_n)
            if not partition.ok:
                # Prefer partial on the current pool first. Under TikTok 429, refill often
                # yields nothing and only delays the k/N path that already has usable clips.
                self._log(f"素材池不够同批互斥分配：{partition.reason}")
                stages["batch_partition_initial"] = {
                    "ok": False, "reason": partition.reason,
                    "unique": partition.unique_count, "need": partition.need,
                }
                partial_now = partition_clips_disjoint(clips, batch_n, allow_partial=True)
                timings = getattr(collection, "timings", None) or {}
                skip_refill = bool(
                    timings.get("partial_pool")
                    or timings.get("discovery_rate_limited")
                    or (
                        partial_now.ok
                        and partial_now.bundles
                        and partition.unique_count <= max(4, requested_n)
                    )
                )
                if skip_refill and partial_now.ok and partial_now.bundles:
                    partition = partial_now
                    stages["collect_refill"] = {
                        "ok": False,
                        "skipped": True,
                        "reason": "池已偏薄或已限流，跳过补采，直接部分交付",
                    }
                else:
                    self._log("尝试补采以提高可交付条数")
                    refill = self._run_collector(request, task_dir, force_min_clips=partition.need)
                    stages["collect_refill"] = {
                        "ok": refill.ok, "reason": refill.reason,
                        "clips": len(refill.clips), "candidates": len(refill.candidates),
                    }
                    # Prefer whatever pool we have (refill or prior) for partial deliver.
                    if refill.ok or refill.clips:
                        collection = refill if (refill.ok or len(refill.clips) >= len(collection.clips)) else collection
                        clips = [_asdict(c) for c in collection.clips]
                        partition = partition_clips_disjoint(clips, batch_n)
                    if not partition.ok:
                        partition = partition_clips_disjoint(clips, batch_n, allow_partial=True)
                    if (not partition.ok or not partition.bundles) and partial_now.ok and partial_now.bundles:
                        partition = partial_now
                if not partition.ok or not partition.bundles:
                    return self._fail(
                        task_id, workflow_job_id,
                        f"同批 {requested_n} 条成片素材不够且无法部分交付："
                        f"{partition.reason}；补采：{stages.get('collect_refill', {}).get('reason', 'n/a')}",
                        delivery_paths=[], audit_path=audit_path,
                    )
            if len(partition.bundles) < requested_n:
                batch_n = len(partition.bundles)
                self._batch_partial_note = f"缺素材，已交付计划 {batch_n}/{requested_n}"
                self._log(self._batch_partial_note + f"：{partition.reason}")
            stages["batch_partition"] = {
                "ok": True, "reason": partition.reason,
                "unique": partition.unique_count, "need": partition.need,
                "requested_count": requested_n,
                "deliver_count": batch_n,
                "partial": batch_n < requested_n,
                "per_video": [len(b) for b in partition.bundles],
                "assignments": [
                    {"video_index": i, "clip_ids": [str(c.get("clip_id")) for c in bundle]}
                    for i, bundle in enumerate(partition.bundles)
                ],
            }
            self._log(partition.reason)
            self._batch_count = batch_n
            self._batch_allowed_ids = [
                {str(c.get("clip_id")) for c in bundle if c.get("clip_id")}
                for bundle in partition.bundles
            ]
            (task_dir / "batch-assignments.json").write_text(
                json.dumps(stages["batch_partition"], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            footage_for_prompt = [
                {k: c.get(k) for k in ("path", "source_url", "account", "title", "duration", "width", "height",
                                       "screen_grid", "screen_times", "usable_segments", "clip_id")}
                for c in clips
            ]
            from .collect import MARKET_LANGUAGE, resolve_ffmpeg
            market = str(request.get("market") or "MX")
            locale, language = MARKET_LANGUAGE.get(market, ("en", "English"))
            export_paths_planned = [
                str(exports_dir / f"{market}-{task_id}-chatcut-{i + 1}.mp4")
                for i in range(batch_n)
            ] if batch_n > 1 else [str(exports_dir / f"{market}-{task_id}-chatcut.mp4")]
            self._export_paths_planned = [Path(p) for p in export_paths_planned]
            export_path = export_paths_planned[0]
            assignments = []
            for i, bundle in enumerate(partition.bundles):
                assignments.append({
                    "video_index": i,
                    "clip_ids": [str(c.get("clip_id")) for c in bundle if c.get("clip_id")],
                    "clips": [
                        {k: c.get(k) for k in (
                            "clip_id", "path", "source_url", "account", "title", "duration",
                            "width", "height", "screen_grid", "screen_times", "usable_segments",
                        )}
                        for c in bundle
                    ],
                })
            extra = {
                "footage_json": json.dumps(footage_for_prompt, ensure_ascii=False, indent=2),
                "batch_assignments_json": json.dumps(assignments, ensure_ascii=False, indent=2),
                "count": str(batch_n),
                "caption_language": language,
                "caption_locale": locale,
                "cta_example": CTA_EXAMPLE.get(market, "BUY NOW!"),
                "export_path": export_path,
                "export_paths_json": json.dumps(export_paths_planned, ensure_ascii=False, indent=2),
                "project_name": f"{MARKET_LABEL.get(market, market)}{request.get('product')} TikTok 无人值守 {task_id[-8:]}",
                "work_dir": str(task_dir / "work"),
                "example_mg_path": str(EXAMPLE_MG_PATH),
                "ffmpeg": resolve_ffmpeg(),
                "product_definition": getattr(collection, "product_definition", "") or str(request.get("product") or ""),
            }
            self._product_definition = extra["product_definition"]
            self._cta = extra["cta_example"]
            (task_dir / "work").mkdir(parents=True, exist_ok=True)
        # For partial deliver, never leave request_json.count at the original N —
        # Codex must see the reduced k so it does not chase N exports with a thin pool.
        prompt_request = dict(request)
        partial_note = getattr(self, "_batch_partial_note", "") or ""
        deliver_n = int(getattr(self, "_batch_count", 0) or prompt_request.get("count") or 1)
        requested_for_prompt = int(
            getattr(self, "_batch_requested_count", 0) or prompt_request.get("count") or deliver_n
        )
        if deliver_n and requested_for_prompt and deliver_n < requested_for_prompt:
            prompt_request["count"] = deliver_n
            prompt_request["requested_count"] = requested_for_prompt
            prompt_request["partial_delivery"] = True
            prompt_request["partial_note"] = (
                partial_note or f"缺素材，已交付计划 {deliver_n}/{requested_for_prompt}"
            )
        prompt = render_produce_prompt(
            request=prompt_request,
            task_id=task_id,
            workflow_job_id=workflow_job_id,
            exports_dir=exports_dir,
            delivery_dir=delivery_dir,
            result_path=result_path,
            template_path=template_path,
            extra=extra,
        )
        (logs_dir / "codex-prompt.md").write_text(prompt, encoding="utf-8")
        # Thin pool: remind Codex this is partial k/N and create_project is mandatory.
        if getattr(self, "_batch_partial_note", "") or (
            int(getattr(self, "_batch_requested_count", 0) or 0)
            > int(getattr(self, "_batch_count", 0) or 0)
            > 0
        ):
            note = getattr(self, "_batch_partial_note", "") or "素材不足，按已分配条数制作"
            thin = "\n\n".join(
                [
                    "",
                    "## 缺素材部分交付（系统已降级）",
                    f"- {note}",
                    "- 只制作 `count` / `export_paths_json` 列出的条数，**不要**按原 request 里更大的目标硬凑。",
                    "- 首轮必须 `create_project` 成功拿到 project_id；若 create_project 失败，结果写 ok=false 并停止，不要假装工程已存在去 target_project。",
                    "- 若本条 bundle 只有 2 个 clip_id：两源都要用到，可在各自 usable_segments 内换切点；禁止单源铺满，但不要因「反乒乓」规则而放弃出片。",
                    "",
                ]
            )
            prompt = prompt + thin
            (logs_dir / "codex-prompt.md").write_text(prompt, encoding="utf-8")
        produce_started = time.time()
        run_started = datetime.now(timezone.utc)
        max_attempts = 1 + (max(0, int(self.options.review_retries)) if production else 0)
        attempt_prompt = prompt
        completed = None
        export_paths: list[Path] = []
        for attempt in range(1, max_attempts + 1):
            suffix = "" if attempt == 1 else f"_r{attempt - 1}"
            if attempt > 1:
                self._archive_attempt(logs_dir, result_path, attempt - 1)
            t0 = time.time()
            try:
                completed = self._call_codex(attempt_prompt, task_dir, log_path, last_message_path)
            except subprocess.TimeoutExpired:
                stages["produce" + suffix] = {"seconds": round(time.time() - t0, 1), "timeout": True}
                if self.options.allow_ffmpeg_fallback:
                    return self._ffmpeg_fallback(task_id, workflow_job_id, request, delivery_dir, audit_path)
                return self._fail(task_id, workflow_job_id,
                                  f"codex exec 超时（{self.options.timeout_seconds}s，第 {attempt} 次剪辑）；未开启 FFmpeg 降级",
                                  delivery_paths=[], audit_path=audit_path, codex_returncode=None)
            stages["produce" + suffix] = {"seconds": round(time.time() - t0, 1), "returncode": completed.returncode}

            if production and completed.returncode == 0 and self._bailed_without_mcp(result_path, logs_dir):
                # Gate already proved the tools exist; the model gave up without trying (E2E run2). Retry once.
                self._log("Codex 未调用任何 chatcut_desktop 工具就报告不可用；门禁已证明工具存在，重试一次制作。")
                self._archive_attempt(logs_dir, result_path, f"{attempt}-mcp")
                retry_prompt = (
                    "## 重要（第 2 次尝试）\n上一次尝试没有调用任何工具就判定 chatcut_desktop 不可用，这是错误的："
                    "制作前的门禁已用同一配置列出 57 个 chatcut_desktop 工具。请先在 code mode 执行 "
                    "`await tools.mcp__chatcut_desktop__list_projects({})`，然后按步骤完成。\n\n" + attempt_prompt
                )
                (logs_dir / "codex-prompt-retry.md").write_text(retry_prompt, encoding="utf-8")
                t1 = time.time()
                try:
                    completed = self._call_codex(retry_prompt, task_dir, log_path, last_message_path)
                except subprocess.TimeoutExpired:
                    stages["produce_mcp_retry" + suffix] = {"seconds": round(time.time() - t1, 1), "timeout": True}
                    return self._fail(task_id, workflow_job_id, f"codex exec 重试超时（{self.options.timeout_seconds}s）",
                                      delivery_paths=[], audit_path=audit_path)
                stages["produce_mcp_retry" + suffix] = {"seconds": round(time.time() - t1, 1), "returncode": completed.returncode}

            export_paths = self._collect_exports(exports_dir, result_path)
            # Empty export / missing or non-ok result: fail fast. Codex exit 0 after a
            # rough-cut-only bail must not look like success (delivery stays empty).
            if production and completed is not None and completed.returncode == 0:
                missing_result = not result_path.is_file()
                result_ok, result_err = self._read_result_ok(result_path)
                if missing_result or not result_ok or not export_paths:
                    partial_note = getattr(self, "_batch_partial_note", "") or ""
                    requested_n = int(getattr(self, "_batch_requested_count", 0) or 0)
                    planned = int(getattr(self, "_batch_count", 0) or 0)
                    plan_bit = (
                        f"缺素材计划交付 {planned}/{requested_n}，"
                        if planned and requested_n and planned < requested_n
                        else (f"{partial_note}，" if partial_note else "")
                    )
                    if missing_result:
                        detail = "结果文件缺失"
                    elif not result_ok:
                        detail = f"结果 ok!=true（{result_err or 'ok missing/false'}）"
                    else:
                        detail = f"exports=0（无 planned/result 路径上的成片；delivery 将为空）"
                    return self._fail(
                        task_id,
                        workflow_job_id,
                        (
                            f"{plan_bit}制作未生成成片文件（{detail}），delivery 为空，拒绝假成功；"
                            f"codex exit={completed.returncode}。"
                            "停止空转重试。请确认 ChatCut 已开、MCP 可用并完成 export 后重投；"
                            "若仍只有极少素材，先改善 TikTok 出口/限流再采。"
                        ),
                        delivery_paths=[],
                        audit_path=audit_path,
                        codex_returncode=completed.returncode,
                    )
            if self.options.forbid_inapp_agent:
                inapp_runs = detect_inapp_agent_runs(self.options.chatcut_agent_log, run_started)
                if inapp_runs:
                    (logs_dir / "inapp-agent-violation.json").write_text(
                        json.dumps(inapp_runs, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    total_cost = sum(float(r.get("cost") or 0) for r in inapp_runs)
                    # Keep any export for human review, but never mark it produced.
                    kept = self._copy_to_delivery(export_paths, delivery_dir) if export_paths else []
                    return self._fail(
                        task_id,
                        workflow_job_id,
                        f"违规：制作期间调用了 ChatCut 应用内 AI Agent {len(inapp_runs)} 次"
                        f"（日志 cost 合计 {total_cost:.4f}，消耗 ChatCut 额度）；仅允许直连 chatcut_desktop MCP。"
                        "导出已留存供复核，状态记为失败。",
                        delivery_paths=kept,
                        audit_path=audit_path,
                        codex_returncode=completed.returncode,
                        renderer="chatcut_inapp_agent",
                    )
            if not production or completed.returncode != 0:
                break

            from .verify_output import verify_production

            t2 = time.time()
            verdict = verify_production(
                result_path=result_path, export_paths=export_paths, clips=clips,
                events_path=logs_dir / "codex-events.jsonl",
                expected_count=int(getattr(self, "_batch_count", 1) or 1),
                allowed_clip_ids_per_video=getattr(self, "_batch_allowed_ids", None) or None,
            )
            stages["verify" + suffix] = {"ok": verdict.ok, "seconds": round(time.time() - t2, 1),
                                         "reason": verdict.reason, "details": verdict.details}
            (logs_dir / f"verify{suffix}.json").write_text(
                json.dumps(stages["verify" + suffix], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            issues: list[str]
            if not verdict.ok:
                issues = [verdict.reason]
                self._log(f"成片校验未通过（第 {attempt} 次）：{verdict.reason}")
                # No file / zero exports cannot be fixed by re-edit retries.
                hard = (
                    "结果文件缺失" in verdict.reason
                    or "实际 0" in verdict.reason
                    or ("应恰好导出" in verdict.reason and len(export_paths) == 0)
                )
                if hard:
                    partial_note = getattr(self, "_batch_partial_note", "") or ""
                    requested_n = int(getattr(self, "_batch_requested_count", 0) or 0)
                    planned = int(getattr(self, "_batch_count", 0) or 0)
                    plan_bit = (
                        f"缺素材计划交付 {planned}/{requested_n}，"
                        if planned and requested_n and planned < requested_n
                        else (f"{partial_note}，" if partial_note else "")
                    )
                    return self._fail(
                        task_id, workflow_job_id,
                        f"{plan_bit}成片校验失败且无可用导出，停止重试：{verdict.reason}",
                        delivery_paths=[], audit_path=audit_path,
                        codex_returncode=completed.returncode,
                    )
            else:
                self._log(verdict.reason)
                t3 = time.time()
                issues, review_payload = self._review(result_path, export_paths, clips, request, task_dir, suffix)
                stages["review" + suffix] = {"ok": not issues, "seconds": round(time.time() - t3, 1),
                                             "issues": issues, "payload": review_payload}
                (logs_dir / f"review{suffix}.json").write_text(
                    json.dumps(stages["review" + suffix], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                if not issues:
                    self._log(f"成片自动复核通过（第 {attempt} 次剪辑）。")
                    break
                self._log(f"成片自动复核未通过（第 {attempt} 次）：{'；'.join(issues)}")
                # Wrong-product / source burned text cannot be fixed by re-editing the same clips.
                if review_issues_need_new_footage(issues):
                    return self._fail(
                        task_id, workflow_job_id,
                        f"成片复核判定素材不合格（错品/原片烧录文字），不重剪同一批素材："
                        f"{'；'.join(issues)}。请换素材后重投。",
                        delivery_paths=[], audit_path=audit_path, codex_returncode=completed.returncode)
            if attempt == max_attempts:
                return self._fail(
                    task_id, workflow_job_id,
                    f"成片复核未通过（已自动重剪 {max_attempts - 1} 次，不交付）：{'；'.join(issues)}",
                    delivery_paths=[], audit_path=audit_path, codex_returncode=completed.returncode)
            # Also stop early when verify itself already flagged burned-text / wrong-product style reasons.
            if review_issues_need_new_footage(issues):
                return self._fail(
                    task_id, workflow_job_id,
                    f"成片校验判定素材不合格（错品/原片烧录文字），不重剪同一批素材："
                    f"{'；'.join(issues)}。请换素材后重投。",
                    delivery_paths=[], audit_path=audit_path, codex_returncode=completed.returncode)
            attempt_prompt = self._feedback_prompt(prompt, issues, result_path, exports_dir, task_id,
                                                   str(request.get("market") or "MX"), attempt)
            (logs_dir / f"codex-prompt-r{attempt}.md").write_text(attempt_prompt, encoding="utf-8")

        if completed.returncode == 0 and export_paths:
            delivered = self._copy_to_delivery(export_paths, delivery_dir)
            qualified = qualifying_delivery_videos(delivery_dir)
            if not qualified:
                return self._fail(
                    task_id,
                    workflow_job_id,
                    (
                        f"delivery 为空或成片过小（<{MIN_DELIVERY_BYTES}B），拒绝标记 produced（防假成功）；"
                        f"codex exit={completed.returncode}，exports={len(export_paths)}，"
                        f"copied={len(delivered)}。"
                    ),
                    delivery_paths=[],
                    audit_path=audit_path,
                    codex_returncode=completed.returncode,
                )
            delivered = [str(p) for p in qualified]
            self._mark_produce_log_delivery_ok(log_path, delivered)
            partial_note = getattr(self, "_batch_partial_note", "") or ""
            requested_n = int(getattr(self, "_batch_requested_count", 0) or 0)
            if partial_note or (requested_n and len(delivered) < requested_n):
                k = len(delivered)
                n = requested_n or k
                reason = (
                    f"ChatCut/Codex 制作完成（model={self.options.codex_model}），"
                    f"缺素材，已交付 {k}/{n}"
                    + (f"（{partial_note}）" if partial_note and f"{k}/{n}" not in partial_note else "")
                )
            else:
                reason = (
                    f"ChatCut/Codex 制作完成（model={self.options.codex_model}），"
                    f"已交付 {len(delivered)} 个文件"
                )
            self._update_state(
                task_id,
                workflow_job_id,
                status="produced",
                reason=reason,
                delivery_paths=delivered,
                renderer="chatcut",
                codex_returncode=completed.returncode,
                audit_path=audit_path,
            )
            self._log(reason)
            self._record_used_footage(request, clips, task_id)
            return ProduceResult(
                status="produced",
                reason=reason,
                delivery_paths=delivered,
                renderer="chatcut",
                codex_returncode=completed.returncode,
                audit_path=str(audit_path) if audit_path else None,
            )

        if self.options.allow_ffmpeg_fallback:
            return self._ffmpeg_fallback(
                task_id,
                workflow_job_id,
                request,
                delivery_dir,
                audit_path,
                prior=f"codex exit={completed.returncode}, exports={len(export_paths)}",
            )

        partial_note = getattr(self, "_batch_partial_note", "") or ""
        requested_n = int(getattr(self, "_batch_requested_count", 0) or 0)
        planned = int(getattr(self, "_batch_count", 0) or 0)
        plan_bit = (
            f"缺素材计划交付 {planned}/{requested_n}，"
            if planned and requested_n and planned < requested_n
            else (f"{partial_note}，" if partial_note else "")
        )
        reason = (
            f"{plan_bit}ChatCut/Codex 制作失败（exit={completed.returncode}，exports={len(export_paths)}）；"
            "未开启 --allow-ffmpeg-fallback。交付仍为 0 个成片，非整单进度。"
        )
        return self._fail(
            task_id,
            workflow_job_id,
            reason,
            delivery_paths=[],
            audit_path=audit_path,
            codex_returncode=completed.returncode,
        )

    def _call_codex(self, prompt: str, task_dir: Path, log_path: Path, last_message_path: Path):
        return self.codex_runner(
            prompt,
            cwd=self.options.project_root,
            log_path=log_path,
            last_message_path=last_message_path,
            timeout=self.options.timeout_seconds,
            codex_bin=self.options.codex_bin,
            model=self.options.codex_model,
            project_root=self.options.project_root,
            extra_writable_dirs=[task_dir, self.inbox.paths.root],
        )

    @staticmethod
    def _archive_attempt(logs_dir: Path, result_path: Path, label: Any) -> None:
        archived = logs_dir / f"attempt{label}"
        archived.mkdir(exist_ok=True)
        for name in ("codex-events.jsonl", "codex-produce.json"):
            if (logs_dir / name).is_file():
                (logs_dir / name).replace(archived / name)
        if result_path.is_file():
            # move it away so a stale result/export can never be re-verified as the retry's output
            result_path.replace(archived / result_path.name)

    def _review(self, result_path: Path, export_paths: list[Path], clips: list[dict[str, Any]],
                request: dict[str, Any], task_dir: Path, suffix: str) -> tuple[list[str], Any]:
        from .batch_footage import extract_per_video_results
        from .vision import check_shots, review_export
        from .collect import resolve_ffmpeg

        result = json.loads(result_path.read_text(encoding="utf-8"))
        issues = check_shots(result, clips)
        reviewer = self.options.reviewer or review_export
        videos = extract_per_video_results(result, export_paths)
        payloads: list[Any] = []
        product_definition = getattr(self, "_product_definition", "") or str(request.get("product") or "")
        cta = getattr(self, "_cta", "")
        ffmpeg = resolve_ffmpeg()
        for index, (export, video) in enumerate(zip(export_paths, videos)):
            # review_export expects top-level shots/captions; synthesize a per-video view.
            one = {
                **result,
                "shots": video.get("shots") or result.get("shots") or [],
                "caption_lines": video.get("caption_lines") or result.get("caption_lines") or [],
                "footage_used": video.get("footage_used") or result.get("footage_used") or [],
            }
            vision_issues, payload = reviewer(
                export, one, request,
                product_definition=product_definition,
                workdir=task_dir / "review" / f"v{index}", ffmpeg=ffmpeg, cta=cta,
                codex_bin=self.options.codex_bin, model=self.options.codex_model,
                name=(f"review{suffix}" if len(export_paths) == 1 else f"review{suffix}-v{index}"),
            )
            payloads.append(payload)
            prefix = "" if len(export_paths) == 1 else f"第 {index + 1} 条："
            issues.extend(prefix + str(i) for i in vision_issues)
        return issues, payloads if len(payloads) > 1 else (payloads[0] if payloads else None)

    @staticmethod
    def _result_has_project(previous: dict[str, Any]) -> bool:
        """True when a prior produce attempt actually recorded a ChatCut project id."""
        if not isinstance(previous, dict):
            return False
        if previous.get("project_id"):
            return True
        for video in previous.get("videos") or []:
            if isinstance(video, dict) and video.get("project_id"):
                return True
        return False

    @staticmethod
    def _feedback_prompt(base_prompt: str, issues: list[str], result_path: Path, exports_dir: Path, task_id: str,
                         market: str, attempt: int) -> str:
        try:
            previous = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        new_export = exports_dir / f"{market}-{task_id}-chatcut-r{attempt}.mp4"
        has_project = Producer._result_has_project(previous)
        issue_blob = "\n- ".join(issues)
        prev_json = json.dumps(previous, ensure_ascii=False, indent=2)[:6000]
        if not has_project:
            # First attempt never created a ChatCut project (create_project failed / no result).
            # Never instruct target_project on a missing id — that yields Project Not Found and 0 exports.
            return (
                f"## 自动重做（第 {attempt + 1} 次剪辑）\n"
                f"上一次**没有成功创建 ChatCut 工程**（无 project_id / 无导出），不能按「改旧工程」重试。"
                f"问题：\n- {issue_blob}\n\n"
                "请**从头新建**：\n"
                "1. `list_projects` 确认连通；\n"
                "2. **必须** `create_project`（按原任务 project_name）；成功后再 `target_project`；\n"
                "3. 不要假设旧工程存在，不要对空 UUID 调 target_project；\n"
                f"4. 导出到 `{new_export}`，并重写结果 JSON（含真实 project_id 与 export_paths）。\n\n"
                f"上一次的结果（可能为空/失败）：\n```json\n{prev_json}\n```\n\n"
                "以下是原始任务说明（规则全部仍然有效）：\n\n" + base_prompt
            )
        project = previous.get("project_id") or "（见 videos[].project_id）"
        return (
            f"## 自动重剪（第 {attempt + 1} 次剪辑）\n"
            f"上一次成片在导出后自动复核**未通过**，不会交付。问题：\n- {issue_blob}\n\n"
            f"请在**同一个工程** `{project}` 上做**最小修改**（先 target_project 打开已有工程；**禁止** create_project 新建、**禁止**删除工程、**禁止**推倒重建整条时间线）："
            "只替换/调整有问题的镜头（换成其它 usable_segments 内的画面，开头和结尾 CTA 用 product_prominent 片段），"
            "复用已有轨道/MG/字幕资源；保持镜头首尾相接、MG 字幕 CUTS 与镜头起点一致（镜头时长变了才重写 captions.jsx 并重新 push_asset、替换 V2 的 MG item）。\n"
            f"导出到新路径 `{new_export}`（不要覆盖旧文件），并重写结果文件（export_paths 只写新路径，shots 按新时间线更新）。\n\n"
            f"上一次的结果：\n```json\n{prev_json}\n```\n\n"
            "以下是原始任务说明（规则全部仍然有效）：\n\n" + base_prompt
        )

    @staticmethod
    def _bailed_without_mcp(result_path: Path, logs_dir: Path) -> bool:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if result.get("ok") or "unavailable" not in str(result.get("error") or ""):
            return False
        from .verify_output import scan_codex_events

        calls = scan_codex_events(logs_dir / "codex-events.jsonl")["calls"]
        return not any(key.startswith("chatcut_desktop.") for key in calls)

    def _record_used_footage(self, request: dict[str, Any], clips: list[dict[str, Any]], task_id: str) -> None:
        """Only a delivered video marks its footage as used; failures never reach here."""
        if not clips:
            return
        from .discover_speed import UsedFootageLedger, infer_form_hints
        seed_dir = self.options.seed_dir or Path(self.options.project_root) / "config" / "discovery-seeds"
        hints = infer_form_hints(str(request.get("product") or ""), str(request.get("notes") or ""),
                                 str(request.get("style") or ""))
        try:
            ledger = UsedFootageLedger.record(seed_dir, request, [str(c.get("clip_id")) for c in clips if c.get("clip_id")],
                                     profile=hints.profile, task_id=task_id)
            if ledger:
                self._log(f"已用素材台账更新（成片已交付）：{ledger}（{len(clips)} 条）")
        except Exception as exc:  # ledger must never break a delivered task
            self._log(f"警告：已用素材台账写入失败（不影响本次交付）：{exc}")

    def _load_ok_collection(self, task_dir: Path):
        """Reuse a prior successful collection.json so resume does not re-download."""
        manifest = task_dir / "collection" / "footage" / "collection.json"
        if not manifest.is_file():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or not payload.get("ok"):
            return None
        clips_data = payload.get("clips") or []
        if not clips_data:
            return None
        for row in clips_data:
            if not Path(str(row.get("path") or "")).is_file():
                return None
        from .collect import Clip, CollectResult

        clip_fields = set(Clip.__dataclass_fields__)
        clips = []
        for row in clips_data:
            if not isinstance(row, dict):
                return None
            kwargs = {k: row[k] for k in clip_fields if k in row}
            try:
                clips.append(Clip(**kwargs))
            except TypeError:
                return None
        return CollectResult(
            ok=True,
            reason=str(payload.get("reason") or "复用已有采集"),
            clips=clips,
            candidates=[],
            keywords=list(payload.get("keywords") or []),
            manifest_path=str(manifest),
            timings=dict(payload.get("timings") or {}),
            product_definition=str(payload.get("product_definition") or ""),
            screened_out=[],
        )

    @staticmethod
    def _load_collection_clips(task_dir: Path) -> list[dict[str, Any]]:
        manifest = task_dir / "collection" / "footage" / "collection.json"
        if not manifest.is_file():
            return []
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        clips = payload.get("clips") if isinstance(payload, dict) else None
        return list(clips) if isinstance(clips, list) else []

    def _run_collector(self, request: dict[str, Any], task_dir: Path, *, force_min_clips: int | None = None):
        from .batch_footage import collect_targets, video_count
        from .collect import collect_footage

        collector = self.options.collector or collect_footage
        seed_dir = self.options.seed_dir
        if seed_dir is None:
            seed_dir = Path(self.options.project_root) / "config" / "discovery-seeds"
        targets = collect_targets(video_count(request))
        min_clips = max(int(self.options.min_clips), targets.min_clips)
        if force_min_clips is not None:
            min_clips = max(min_clips, int(force_min_clips))
        return collector(
            request,
            task_dir / "collection",
            codex_bin=self.options.codex_bin,
            model=self.options.codex_model,
            discover_timeout=self.options.discover_timeout_seconds,
            discover_attempts=self.options.discover_attempts,
            discover_rounds=max(int(self.options.discover_rounds), targets.discover_rounds),
            max_searches=max(int(self.options.discover_max_searches), targets.max_searches),
            min_clips=min_clips,
            max_clips=targets.max_clips,
            max_candidates=targets.max_candidates,
            seed_dir=seed_dir,
            log=self._log,
        )

    def _write_auto_approve_audit(
        self,
        audit_dir: Path,
        task_id: str,
        workflow_job_id: str,
        request: dict[str, Any],
    ) -> Path:
        payload = {
            "policy": "unattended_auto_approve",
            "enabled": True,
            "actor": "unattended-policy",
            "decided_at": _utc_now(),
            "task_id": task_id,
            "workflow_job_id": workflow_job_id,
            "market": request.get("market"),
            "product": request.get("product"),
            "note": (
                "无人值守策略自动放行中文稿/音色关口，允许继续制作；"
                "这不是冒充真人用户的签字，事后可抽查本审计文件。"
            ),
        }
        path = audit_dir / "unattended-auto-approve.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _read_result_ok(result_path: Path) -> tuple[bool, str]:
        """Return (ok_true, error_detail) from produce-result.json."""
        if not result_path.is_file():
            return False, "结果文件缺失"
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"结果不是 JSON：{exc}"
        if not isinstance(payload, dict):
            return False, "结果根节点不是对象"
        if payload.get("ok") is True:
            return True, ""
        return False, str(payload.get("error") or "ok!=true")

    @staticmethod
    def _mark_produce_log_delivery_ok(log_path: Path, delivery_paths: list[str]) -> None:
        """Rewrite produce-stage log so ok reflects delivery-complete, not raw exit."""
        if not log_path.is_file():
            return
        try:
            payload = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        payload["ok"] = True
        payload["delivery_paths"] = list(delivery_paths)
        payload["note"] = "ok=true：qualifying delivery 已落盘"
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _collect_exports(self, exports_dir: Path, result_path: Path) -> list[Path]:
        """Collect export mp4s from result.export_paths and/or planned paths only.

        Blind exports_dir.rglob is removed: leftover orphans must not become a
        false success when Codex rough-cuts and exits 0 without a real export.
        """
        found: list[Path] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
                return
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                return
            seen.add(key)
            found.append(path)

        if result_path.is_file():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                for item in payload.get("export_paths") or []:
                    _add(Path(str(item)))

        planned = list(getattr(self, "_export_paths_planned", None) or [])
        for path in planned:
            _add(Path(path))

        # Severely restricted fallback: only planned basenames directly under exports_dir
        # (no recursive orphan harvest).
        if not found and planned and exports_dir.is_dir():
            names = {Path(p).name for p in planned}
            for path in sorted(exports_dir.iterdir()):
                if path.name in names:
                    _add(path)
        return found

    def _copy_to_delivery(self, export_paths: list[Path], delivery_dir: Path) -> list[str]:
        delivered: list[str] = []
        for source in export_paths:
            destination = delivery_dir / source.name
            if destination.exists():
                stem, suffix = destination.stem, destination.suffix
                index = 1
                while True:
                    candidate = delivery_dir / f"{stem}.{index}{suffix}"
                    if not candidate.exists():
                        destination = candidate
                        break
                    index += 1
            shutil.copy2(source, destination)
            manifest = {
                "source": str(source),
                "destination": str(destination),
                "sha256": _sha256_file(destination),
                "copied_at": _utc_now(),
                "renderer": "chatcut",
            }
            (delivery_dir / (destination.name + ".sha256.json")).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            delivered.append(str(destination))
        return delivered

    def _ffmpeg_fallback(
        self,
        task_id: str,
        workflow_job_id: str,
        request: dict[str, Any],
        delivery_dir: Path,
        audit_path: Path | None,
        *,
        prior: str = "",
    ) -> ProduceResult:
        reason = (
            "已开启 allow_ffmpeg_fallback，但阶段2 MVP 尚未接线 run_local_batch；"
            f"拒绝静默降级。prior={prior or 'codex失败'}"
        )
        marker = {
            "ok": False,
            "renderer": "ffmpeg_fallback",
            "configured": False,
            "reason": reason,
            "request": {
                "task_id": task_id,
                "workflow_job_id": workflow_job_id,
                "product": request.get("product"),
                "market": request.get("market"),
            },
        }
        (delivery_dir / "ffmpeg-fallback-not-configured.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self._fail(
            task_id,
            workflow_job_id,
            reason,
            delivery_paths=[],
            audit_path=audit_path,
            renderer="ffmpeg_fallback",
        )

    def _fail(
        self,
        task_id: str,
        workflow_job_id: str,
        reason: str,
        *,
        delivery_paths: list[str],
        audit_path: Path | str | None = None,
        codex_returncode: int | None = None,
        renderer: str | None = None,
    ) -> ProduceResult:
        audit = str(audit_path) if audit_path else None
        self._update_state(
            task_id,
            workflow_job_id,
            status="produce_failed",
            reason=reason,
            delivery_paths=delivery_paths,
            renderer=renderer,
            codex_returncode=codex_returncode,
            audit_path=audit,
        )
        self._log(reason)
        return ProduceResult(
            status="produce_failed",
            reason=reason,
            delivery_paths=delivery_paths,
            renderer=renderer,
            codex_returncode=codex_returncode,
            audit_path=audit,
        )

    def _update_state(
        self,
        task_id: str,
        workflow_job_id: str,
        *,
        status: str,
        reason: str,
        delivery_paths: list[str],
        renderer: str | None,
        codex_returncode: int | None,
        audit_path: str | Path | None,
    ) -> None:
        existing = self.inbox.read_task_state(task_id) or {}
        now = _utc_now()
        existing.update(
            {
                "task_id": task_id,
                "workflow_job_id": workflow_job_id,
                "status": status,
                "reason": reason,
                "updated_at": now,
                "produce": {
                    "mode": self.options.mode,
                    "stages": getattr(self, "_stages", {}),
                    "renderer": renderer,
                    "delivery_paths": delivery_paths,
                    "codex_returncode": codex_returncode,
                    "codex_model": self.options.codex_model,
                    "audit_path": str(audit_path) if audit_path else None,
                    "finished_at": now,
                },
            }
        )
        if status == "produced":
            existing["completed_at"] = now
        if status == "produce_failed":
            existing["failed_at"] = now
        self.inbox.write_task_state(task_id, existing)
