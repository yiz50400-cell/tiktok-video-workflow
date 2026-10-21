# 分镜与 TikTok 广告结构分析模块

状态：独立 CLI 已完成并对 SHEGLAM Color Bloom 的 Eli/Isra 本地视频实跑；尚未接共享任务库。模块不发网络请求、不调用收费 API，也不假装后台拥有 Codex 语义模型。时间：2026-10-09（Asia/Shanghai）。

## 运行接口

依赖项目现有 `.venv` 的 `imageio-ffmpeg`；分析模块未更改项目主依赖。系统级 `ffmpeg`、`ffprobe`、OpenCV 和 Pillow 均非本模块必需。

```bash
cd <项目目录>
.venv/bin/python scripts/analyze_video.py inspect \
  --input projects/sheglam-color-bloom/watermark-demo/eli-player.mp4 \
  --output projects/sheglam-color-bloom/analysis-dev/eli \
  --source-url 'https://www.tiktok.com/@elimonsivaisg/video/7491088914187357495' \
  --source-evidence projects/sheglam-color-bloom/analysis/eli-page.txt \
  --sample-every 1.7 --max-frames 36

.venv/bin/python scripts/analyze_video.py annotate \
  --manifest projects/sheglam-color-bloom/analysis-dev/eli/manifest.json \
  --observations projects/sheglam-color-bloom/analysis-dev/eli-observations-input.json
```

`inspect` 输出 JSON 摘要，写入 `manifest.json`、`frames/frame-*.jpg`、`contact-*.jpg`。`annotate` 读取本会话看过真实取帧后提供的 JSON，校验源 SHA-256、时间、帧 ID 及证据状态，写入 `observations.json`、`report.md` 与最终索引 `analysis.json`。索引状态为 `codex_observed_needs_review`，ASR/OCR 仍是 `not_run`，并携带三个结果文件的 SHA-256。输出目录已有 manifest 时拒绝覆盖，保证原片和已有分析不被误改。

`--scene-threshold` 默认 0.25，`--min-shot` 默认 0.35 秒，`--sample-every` 默认 3 秒，`--max-frames` 默认 60。像素变化点是镜头切换*候选*，不能代替商品动作边界；连续上脸可能跨数个候选，也可能在单个长镜头内完成。联系图按从左到右、从上到下对应 manifest 的 `contact_sheets[].cells[]`，每格映射帧 ID、取帧 seek 时间、单帧路径和单帧 SHA-256。seek 时间有约一帧误差，精确剪辑点需回放核准。

## 语义输入格式

```json
{
  "source_sha256": "与 manifest.source.sha256 相同",
  "observer": "Codex 本会话，观察实际联系图",
  "observed_at": "2026-10-09T15:09:00+08:00",
  "segments": [
    {
      "kind": "action",
      "evidence_status": "observed",
      "start_seconds": 6.78,
      "end_seconds": 10.6,
      "description": "画面可见脸颊点涂和刷具晕染",
      "frame_ids": ["f0009", "f0010"]
    }
  ],
  "known_facts": [],
  "popularity_hypotheses": [],
  "unknowns": [],
  "risks": []
}
```

`kind` 限定为 `hook/pain/action/proof/rhythm/cta/risk`，证据状态为 `observed/hypothesis/unknown`。`observed` 必须引用区间内的真实帧；`unknown` 不得带帧当作证明。页面互动、合作标记来自来源页文本，要在事实中注明来源，不能从帧倒推出播放量或销量。当前 ASR/OCR 状态恒为 `not_run`；观察者可以描述肉眼确见画面，但不得把未识别口播/字幕写成逐字稿。

## 真素材验收

| 素材 | 源 SHA-256 | 时长 | 镜头候选 | 取帧 | 报告 |
|---|---|---:|---:|---:|---|
| Eli 干净播放版 | `abb6098af41cb8b27f55bee6f346458979114dbfa914d5c9f93dab1e5b7be901` | 18.13s | 14 | 25 | Eli 报告（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/eli/report.md`） |
| Isra 原生下载 | `2f68f59570dfcd180e82faedb51edd7fa8de174c4ec6c5df1ecaee19e8e69680` | 105.12s | 14 | 41 | Isra 报告（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/isra/report.md`） |

### 可见结构与取用判断

- Eli：0–3.10s 面部点涂/刷具与反应作视觉钩子；5.67–10.60s 液体腮红点涂、刷具晕染；10.60–18.13s 多角度妆效近景及持瓶。0–3.10s 紫色灯光和表情适合研究钩子，不宜作标准肤色证明。具体痛点、口播和购买 CTA 均未确认。
- Isra：0–10.17s 真人近景讲述、约 8s 出现包装；31.83–40.02s 涂抹头特写；40.02–60.10s 点涂/手指晕染；60.10–84.10s 后续刷具处理；84.10–100.28s 脸颊妆效和持瓶。40.02–85.38s 是一个约 45 秒像素切换候选区间，动作并未停滞。原片有 TikTok 水印，长讲述段和复用权是剪辑风险。
- Isra 来源页显示约 99.3K 赞，但这只是历史高互动；Eli 来源页约 9895 赞。两条均无播放/转化/销量及账号基线，高互动原因只能列假设，不能宣称爆火因果或高转化。两条都只可确认 Color Bloom 系列，单一色号/SKU 未锁定。Isra 页面 `¿Qué te parece?` 是互动提问，非已核实下单 CTA。

可直接查看 Eli 联系图 1（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/eli/contact-01.jpg`）、Eli 联系图 2（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/eli/contact-02.jpg`）、Isra 联系图 1（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/isra/contact-01.jpg`）、Isra 联系图 2（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/isra/contact-02.jpg`）、Isra 联系图 3（本机证据，未随仓库：`projects/sheglam-color-bloom/analysis-dev/isra/contact-03.jpg`）。每个报告也包含全部联系图和逐格时间索引。

## 主会话接入建议

由主会话在共享 `workflow/cli.py`/`store.py`/`api.py` 等处接入，不由本模块并发修改。建议以 `analysis.json` 作为入口，将 `manifest.json` 和 `observations.json` 作为独立版本化资产存路径、SHA-256、状态和生成时间，先处于 `ANALYSIS_REVIEW`；人工确认语义观察后才可引用对应报告做后续时间计划。若源视频、观察 JSON、目标 SKU 或已批准内容变化，生成新版本并重新审核。镜头候选与语义动作区间分别储存，不把 `shot_id` 当作 `action_id`。`media_analysis_status` 原样呈现，不能在共享任务上标记 ASR/OCR 已完成。

## 验证与限制

```bash
.venv/bin/python -m pytest -q tests/test_analysis.py
.venv/bin/python -m pytest -q tests/test_analysis.py tests/test_cli.py tests/test_api.py tests/test_mcp.py
```

本模块 9 项测试通过，覆盖取帧可追溯、镜头区间、原片保留、无 ASR/OCR 声称、hash/区间/帧 ID 拒绝、源文件变更拒绝与已有分析不覆盖；与现有 CLI/API/MCP 测试合跑共 26 项通过。全仓 `pytest` 当前在收集另一开发会话的 `tests/test_repair.py` 时缺少 `cv2`，这是修补模块隔离依赖问题，不在本会话改主环境。语义判断基于当前取帧和已有来源页文本，不等于音频逐字审听、逐帧 OCR 或合法复用许可。未制作新广告稿、配音、成片或发布。
