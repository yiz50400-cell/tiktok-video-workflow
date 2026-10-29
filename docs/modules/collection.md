# 01 商品视频采集模块

更新：2026-10-09。用户已确认 SHEGLAM 采集方向和既有样例。本模块继承现有候选、来源证据、原片及兼容预览，只做可复用调用和技术回归；不要求重新确认素材方向。下一业务可见阶段是分镜与广告结构分析。

## 独立入口

`workflow.collection.collect(...)` 返回 JSON 可序列化报告；`scripts/collect_videos.py` 是独立 CLI，不写共享任务库。报告固定保存在 `--output-dir/collection.json`，所有媒体路径为绝对路径。

```bash
.venv/bin/python scripts/collect_videos.py \
  --seed-file projects/sheglam-color-bloom/analysis/candidates.json \
  --brand SHEGLAM --product 'Color Bloom Liquid Blush' \
  --output-dir projects/sheglam-color-bloom/collection-dev --max-candidates 3 \
  --existing-raw eli=projects/sheglam-color-bloom/watermark-demo/eli-player.mp4 \
  --existing-preview eli=projects/sheglam-color-bloom/watermark-demo/eli-clean.mp4 \
  --existing-raw isra=projects/sheglam-color-bloom/materials/isra-original.mp4 \
  --existing-preview isra=projects/sheglam-color-bloom/previews/isra.mp4 \
  --existing-raw valeria=projects/sheglam-color-bloom/materials/valeria-original.mp4 \
  --existing-preview valeria=projects/sheglam-color-bloom/previews/valeria.mp4
```

这条命令默认不联网、不复制或重转码。它按旧证据筛选候选、去重、哈希原片，并验证现有预览的 H.264/AAC/yuv420p、faststart 与实际帧解码。`--local-source ID=PATH` 可复制导入新文件并生成预览；`--url URL --resolve-urls` 可在线解析已给 URL；`--download` 用 yt-dlp 对尚无原片的 TikTok URL 有限重试下载并生成兼容预览。`--max-candidates` 限制为 1–8。下载只保存独立原片，不改源文件；预览用本地 imageio-ffmpeg。

运行依赖：已有文件登记用项目 `.venv` 中的 `imageio-ffmpeg`；在线解析/下载另外要求 PATH 中可调用 `yt-dlp`（本机实测版本 2026.08.19）。本模块没有并发修改项目主依赖清单。

## 状态与证据

- `discovery.source_adapter=saved_candidate_evidence` 表示从已有候选证据发现，`automatic_tiktok_search=not_implemented`；这不是自动爆款搜索。账号页 yt-dlp 发现曾遇到 SSL EOF，未将其计为可用来源。
- `url_resolution_status=saved_evidence_only` 表示继承之前页面记录，没有在本次联网重新解析；显式解析才标 `resolved` 或 `failed`，失败原因独立保存。
- `acquisition_status` 记录 `existing_validated`、`imported`、`downloaded` 或 `failed`。`preview_status` 单列 `validated`/`failed`，所以原片已取得而预览失败不会变成假成功，也不会丢原片路径/哈希。
- `watermark_status=pending_visual_inspection` 是新流程默认值；yt-dlp 页面/下载成功不代表无水印。`prior_watermark_evidence` 保留旧记录。Eli 的已有干净版本与抽帧检查见 `watermark-demo/verification.json`，不扩大到别的视频。
- `visible_metrics` 只保存旧页面可见值，缺失指标为 `null` 并在 `metric_missing_reasons` 写原因。账号基线、当前增长、销量与因果热度没有证据，`popularity_evidence=null`。历史 Isra 高互动不是当前爆款证明。
- `product_match_level=same_product_family` 仅覆盖 Color Bloom 系列，`sku_verification_status=unverified`，色号/配方和复用权未核实。异商品官方粉底棒只作结构参考，不能作为腮红素材或功效证据。
- 同视频 ID/去参数 URL 在候选层合并；不同 URL 下载后同哈希在 `duplicate_content_of` 标记。原片和预览都不因去重自动删除。

## 本次技术结果

报告：`projects/sheglam-color-bloom/collection-dev/collection.json`。已存证据 4 条，限定输出 3 条。Eli、Isra、Valeria 均为 `existing_validated`，URL 状态均为 `saved_evidence_only`，原片哈希已写入；三份既有预览均通过编码/faststart/首帧解码检查。Eli/Isra 是同系列商业候选，Valeria 的“合作未确认”没有被误判为已合作，仅列结构参考。官方粉底棒仍保留在输入证据，但不在本次 3 条输出中。中途曾在范围纠正前对 Eli 做一次联网下载适配验证；已停止重复批次并清理该次产生的副本，本报告引用此前保存的文件。没有重新执行批量搜索或下载。

回归：`.venv/bin/python -m pytest tests/test_collection.py tests/test_api.py tests/test_cli.py tests/test_mcp.py -q`，本次 26 项通过。覆盖 URL 去重、内容哈希去重、异商品/未确认合作筛除、下载失败、已有文件零联网、预览失败保留原片状态、缺失热度指标。`pytest -q` 全量收集被并行修补模块的 `tests/test_repair.py` 缺少 `cv2` 阻断；没有修改共享依赖或该模块文件。

## 主会话接入

主会话读取 `collection.json` 的 `candidates`，以 `raw_path`、`preview_path`、`sha256` 和四类状态作为资产入库条件；仅 `preview_status=validated` 才展示预览。`material_eligibility` 是同系列候选资格，不是 SKU、复用权或最终镜头批准。不得把 `saved_evidence_only` 当成实时 URL 解析，也不得把候选发现当成自动高表现搜索。共享 API/CLI/MCP、配置、依赖及 `.runtime` 入库由主会话负责；本模块没有并发改写。

TikTok 页 URL 自动发现已接本机 Evil（见 AGENTS / skill 硬门与 Docker与Evil0ctal自装）；账号级数据源与付费投放证据仍按实际能力，未实现的来源保持显式缺项。若以后有新素材或影响画面效果的参数变化，再展示对应新产物，不重验这批既有素材。
