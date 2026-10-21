
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflow.unattended import Inbox, Supervisor
from workflow.unattended.producer import CodexModel, ProduceOptions
from workflow.unattended.text_request import TextRequestParseError, parse_enqueue_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地无人值守收件箱与调度控制面（阶段1+阶段2制作MVP）")
    parser.add_argument("--data-dir", help="覆盖 WORKFLOW_DATA_DIR；默认使用项目 .runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue = subparsers.add_parser(
        "enqueue",
        help="原子投递一条请求（推荐 --text 一句话，或 --file JSON）",
    )
    enqueue.add_argument(
        "--text",
        nargs="?",
        const="",
        help="一句话投递，如「墨西哥唇釉做2条，自然带货」；省略值或传入 - 时从 stdin 读取",
    )
    enqueue.add_argument("--file", help="读取现有 JSON 文件")
    enqueue.add_argument("--title")
    enqueue.add_argument("--product")
    enqueue.add_argument("--market", choices=["MX", "BR", "ID", "PH"])
    enqueue.add_argument("--count", type=int)
    enqueue.add_argument("--style")
    enqueue.add_argument("--notes")
    enqueue.add_argument("--task-id")
    enqueue.add_argument("--media-path", action="append", default=[])
    enqueue.add_argument("--concept", action="append", default=[])

    subparsers.add_parser("list", help="列出各桶文件与任务状态摘要")
    once = subparsers.add_parser("once", help="健康检查后处理最多一条任务（默认只 create）")
    _add_produce_flags(once)
    run = subparsers.add_parser("run", help="once 的兼容别名")
    _add_produce_flags(run)
    watch = subparsers.add_parser("watch", help="持续轮询收件箱")
    watch.add_argument("--interval", type=float, default=30.0, help="轮询秒数，默认 30")
    _add_produce_flags(watch)
    return parser


def _add_produce_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--produce",
        action="store_true",
        help="create 成功后继续调用 Codex(-m gpt-5.6-sol)+ChatCut 制作；默认关闭",
    )
    parser.add_argument(
        "--unattended-auto-approve",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="制作时写入 unattended_auto_approve 审计（默认开启；--no-unattended-auto-approve 关闭）",
    )
    parser.add_argument(
        "--allow-ffmpeg-fallback",
        action="store_true",
        help="ChatCut/Codex 失败时尝试 FFmpeg 降级（MVP 仅记录未接线，默认关）",
    )
    parser.add_argument("--codex-bin", default="codex", help="codex 可执行文件，默认 PATH 中的 codex")
    parser.add_argument("--codex-model", default=None, help="codex exec -m 模型；优先级：本参数 > 环境变量 VW_CODEX_MODEL > 默认 gpt-5.6-sol")
    parser.add_argument("--produce-timeout", type=float, default=900.0, help="制作超时秒数，默认 900")
    parser.add_argument("--canary", action="store_true",
                        help="金丝雀模式：只导出 TEST-无人值守-20261017 短片验证 MCP 链路，不采集、不成片校验")
    parser.add_argument("--discover-timeout", type=float, default=900.0, help="采集检索（Codex web search）超时秒数，默认 900")
    parser.add_argument("--min-clips", type=int, default=3, help="最少合格 TikTok 素材条数，默认 3")


def _read_text_arg(value: str | None) -> str:
    if value is None:
        raise ValueError("未提供 --text")
    if value in {"", "-"}:
        raw = sys.stdin.read()
        if not raw.strip():
            raise TextRequestParseError(
                "标准输入为空；请传入一句话，例如：墨西哥唇釉做2条，自然带货"
            )
        return raw
    return value


def _payload_from_args(args: argparse.Namespace) -> dict:
    text_requested = getattr(args, "text", None) is not None
    if text_requested and args.file:
        raise ValueError("请只使用 --text 或 --file 之一，不要同时指定")
    if text_requested:
        payload = parse_enqueue_text(_read_text_arg(args.text))
        # optional field overrides after parse
        if args.title:
            payload["title"] = args.title
        if args.product:
            payload["product"] = args.product
        if args.market:
            payload["market"] = args.market
        if args.count is not None:
            payload["count"] = args.count
        if args.style is not None:
            payload["style"] = args.style
        if args.notes is not None:
            payload["notes"] = args.notes
        if args.task_id:
            payload["task_id"] = args.task_id
        if args.media_path:
            payload["media_paths"] = args.media_path
        if args.concept:
            payload["concepts"] = args.concept
        return payload
    if args.file:
        return json.loads(Path(args.file).read_text(encoding="utf-8"))
    missing = [name for name in ("title", "product", "market", "count") if getattr(args, name) in (None, "")]
    if missing:
        raise ValueError(
            "缺少必要参数：" + "、".join(missing)
            + "。也可改用：enqueue --text '墨西哥唇釉做2条，自然带货'"
        )
    payload = {
        "title": args.title,
        "product": args.product,
        "market": args.market,
        "count": args.count,
    }
    for key in ("style", "notes"):
        value = getattr(args, key)
        if value is not None:
            payload[key] = value
    if args.task_id:
        payload["task_id"] = args.task_id
    if args.media_path:
        payload["media_paths"] = args.media_path
    if args.concept:
        payload["concepts"] = args.concept
    return payload


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    inbox = Inbox(args.data_dir)
    try:
        if args.command == "enqueue":
            payload = _payload_from_args(args)
            destination = inbox.enqueue(payload)
            print(f"[无人值守] 已原子投递：{destination}")
            if getattr(args, "text", None) is not None:
                print(
                    "[无人值守] 解析结果："
                    f"market={payload.get('market')} product={payload.get('product')} "
                    f"count={payload.get('count')} title={payload.get('title')} "
                    f"style={payload.get('style') or '-'}"
                )
            return 0
        if args.command == "list":
            summary = inbox.list_summary()
            labels = {
                "incoming": "待处理（incoming≈pending）",
                "processing": "处理中",
                "done": "完成",
                "failed": "失败",
                "conflict": "冲突",
            }
            for bucket, files in summary["buckets"].items():
                print(f"{labels[bucket]}：{len(files)}")
                for filename in files:
                    print(f"  - {filename}")
            print("任务状态：")
            if not summary["states"]:
                print("  （暂无）")
            for state in summary["states"]:
                print(
                    f"  - {state['task_id']} | {state.get('status')} | "
                    f"job={state.get('workflow_job_id') or '-'} | {state.get('reason') or '-'}"
                )
            return 0
        supervisor = Supervisor(data_dir=args.data_dir)
        produce_options = ProduceOptions(
            unattended_auto_approve=bool(getattr(args, "unattended_auto_approve", True)),
            allow_ffmpeg_fallback=bool(getattr(args, "allow_ffmpeg_fallback", False)),
            codex_bin=getattr(args, "codex_bin", "codex"),
            codex_model=CodexModel.resolve(getattr(args, "codex_model", None)),
            timeout_seconds=float(getattr(args, "produce_timeout", 900.0)),
            project_root=PROJECT_ROOT,
            mode="canary" if getattr(args, "canary", False) else "production",
            discover_timeout_seconds=float(getattr(args, "discover_timeout", 900.0)),
            min_clips=int(getattr(args, "min_clips", 3)),
        )
        produce = bool(getattr(args, "produce", False))
        if args.command in {"once", "run"}:
            result = supervisor.run_once(produce=produce, produce_options=produce_options)
            ok = {
                "succeeded",
                "produced",
                "duplicate",
                "idle",
                "deferred",
                "locked",
            }
            return 0 if result["status"] in ok else 1
        if args.command == "watch":
            supervisor.watch(
                args.interval,
                produce=produce,
                produce_options=produce_options,
            )
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[无人值守] 错误：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
