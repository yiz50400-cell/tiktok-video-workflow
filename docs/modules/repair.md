# 02 本地 LaMa 修补模块

更新：2026-10-09。用户已接受既有 Eli 3–9 秒 LaMa 样片；本次只封装可重复调用与技术回归，不重跑该 6 秒内容，不要求重验自然度。业务下一步是分镜/广告结构分析。

## 已接受结果与本轮边界

- 已接受的原型产物：`projects/sheglam-color-bloom/watermark-fallback-trial-v2/lama.mp4`，对应 `trial.json` 与 `comparison.mp4`，已有资产 `asset_b9c1bd2a4879`。接受范围仅此 6 秒 LaMa 效果，不是 Telea、全片或未来任意修补。
- 新模块沿用原型 `experiments/watermark-fallback/eli-regions.json`（已移入本机 `_archive/legacy-20261017/experiments/`，未随仓库）的校准算法、阈值、7×7 膨胀、320 像素局部 LaMa、模型 `.runtime/models/big-lama.pt`（MD5 `e3aa4aaa15225a33ec84f9f4bc47e500`）。输入只读取带水印源；不读取干净版作为修补依据。
- 本轮唯一真实推理仅在 Eli 带水印源的 3.0–3.2 秒，共 6 帧，属于内部接口/编码检查。首帧 JPEG 与原型已接受样片相同时间点的 LaMa 静帧逐像素一致。新短片状态仍为 `NEEDS_VISUAL_REVIEW`；此状态不撤销也不扩大已有 6 秒验收。
- 通用任意水印检测尚未验证。现有 `calibrated` 仅复用特定素材/区域的白字检测；`box`、`keyframes`、`mask_image` 是显式输入，不是自动定位。

## 调用

修补依赖只在 `uv --no-project` 隔离环境中解析，按 `workflow/repair/requirements.txt` 精确版本运行，不修改项目 `.venv`。在 `<项目目录>` 中执行；下例的源片、遮罩与输出目录均为开发机本机证据（未随仓库），实际使用时替换为本机文件，模型放在 `.runtime/models/big-lama.pt`：

```sh
uv run --no-project --with-requirements workflow/repair/requirements.txt \
  python scripts/repair_video.py run \
  --source <带水印源视频路径> \
  --mask-spec <遮罩JSON路径> \
  --model .runtime/models/big-lama.pt \
  --output-root <修补输出目录> \
  --start 3 --duration 0.2

uv run --no-project --with-requirements workflow/repair/requirements.txt \
  python scripts/repair_video.py status \
  --output-root <修补输出目录>
```

 Python 接口：`workflow.repair.run_repair(source: Path, spec_path: Path, model: Path, output_root: Path, start: float, duration: float, device='auto') -> dict`；`task_status(output_root: Path, task_id: str | None = None) -> list[dict]`。设备默认自动选择：CUDA 优先，其次 Apple MPS，最后 CPU；也可用 CLI 的 `--device` 固定。CLI 输出同一 JSON 结构；出错返回非零退出码，运行期异常详见任务目录 `task.json` 的 `error`。

## 遮罩输入

JSON 顶层必须有与源视频一致的 `dimensions: [width, height]` 和非空 `regions`。区域 `name` 唯一。现有 `eli-regions.json` 无 `mode` 但带 `calibration_seconds`，自动按 `calibrated` 解析。所有 `box` 均为 `[x, y, width, height]`，原点在左上；`second` 是源视频绝对秒数。

```json
{
  "dimensions": [576, 1024],
  "regions": [
    {"name": "fixed", "mode": "box", "box": [10, 466, 162, 80]},
    {"name": "moving", "mode": "keyframes", "keyframes": [
      {"second": 3.0, "box": [10, 466, 162, 80]},
      {"second": 8.0, "box": [404, 710, 170, 82]}
    ]}
  ],
  "protect_regions": [[200, 400, 80, 80]]
}
```

`keyframes` 的矩形坐标/尺寸做线性插值；区间外保持首/末关键帧。`mask_image` 用 `file` 指向相对 JSON 位置的灰度 PNG，像素值 >127 为遮罩，文件内容计入任务指纹。`calibrated` 用 `box`、`calibration_seconds` 和可选局部 `colored_logo_boxes`，对每帧左右候选取最高白字得分，得分低于 0.55 则该校准区不遮罩。不同模式可组合；遮罩与 `protect_regions` 有交叠则失败，不静默擦掉保护区。

## 任务与产物

任务 ID 为参数指纹：模块参数版本 `repair-lama-v1` + 源 SHA-256 + 模型 MD5 + 遮罩 JSON/关联图片哈希 + 起始帧/帧数 + 编码参数。同参数且三份输出 SHA-256 均匹配时返回 `reused: true`，不重复推理。参数变更新建任务目录；原实验目录不覆盖。开发者更改会影响画面的算法、阈值或编码后必须提升 `PARAM_VERSION`，避免误复用旧像素结果。

每个任务目录包含：

- `repaired.mp4`：LaMa 候选，源片对应片段的音轨重组；
- `mask.mp4`：原片加红色遮罩可视化，无音轨；
- `comparison.mp4`：左原片、右修补，带源片音轨；
- `mask-*.jpg` / `repaired-*.jpg`：首/中/末帧抽查；
- `task.json`：绝对路径、源哈希、模型/遮罩指纹、状态、尝试次数、历史与错误、输出 SHA-256、完整解码结果；
- `frames.json`：逐帧位置评分与遮罩像素数。

输出成功前检查：源文件哈希未变化、编码前遮罩外像素完全一致、所选区间至少一帧命中遮罩、三份视频全帧音视频解码且帧数准确。模型 MD5、尺寸、时间、区域越界及保护区冲突会明确失败。失败/`KeyboardInterrupt` 保留 `FAILED`/`INTERRUPTED` 与错误记录；查询发现无进程持锁的陈旧 `RUNNING` 会标记为 `INTERRUPTED`。重跑同一指纹会清除本模块旧临时目录、增加尝试号并保留历史，不删除已接受原型。输入验证阶段失败尚未有可靠任务指纹，CLI 直接返回错误而不写任务记录。

## 本轮技术回归

- 命令：上面的 `--start 3 --duration 0.2`；新任务 ID `bc5cde40452b8415b5b8`，实算约 15.13 秒（本机 CPU，含编码校验），再次运行约 0.4 秒返回 `reused: true`、`attempt: 1`。
- 源 SHA-256：`30668bea710a6855c13acfd10e90be6dab53e22d13e6238784f1029fa033ae6f`；模型 MD5 与公开适配记录一致。
- 产物：`projects/sheglam-color-bloom/repair-dev/bc5cde40452b8415b5b8/repaired.mp4`、`mask.mp4`、`comparison.mp4`。三者均 6 帧、全解码通过；源/修补/对比音轨可解码，遮罩视频设计上无音轨。
- 测试：`uv run --no-project --with-requirements workflow/repair/requirements.txt python -m pytest -q tests/test_repair.py`，10 项通过。涵盖无效规格/时间/模型、保护区、移动遮罩、外部图片变更、原件不变、同参复用、失败恢复、中断、陈旧运行、坏产物重算与解码。
- 这 0.2 秒结果是技术检查，不请求用户重新评价已接受的自然度。未来新素材或改变效果的参数仍需展示候选并做视觉验收；LaMa 单帧修补可能在人物/商品结构及相邻帧出现重建误差。

## 主会话接入建议

只在干净原片获取失败且持有带水印源时调用；业务资产引用 `repaired.mp4` 与 `comparison.mp4`，记录本模块 `task_id`、`task.json` 路径、源哈希、参数版本。新任务统一标记 `NEEDS_VISUAL_REVIEW`，不得因进程成功、此前已接受 Eli 样片或文件可解码而自动批准剪辑。已接受原型资产 `asset_b9c1bd2a4879` 保持原验收范围，不由本模块重新生成或改写。共享 `workflow/api.py`、`cli.py`、`store.py`、`mcp_server.py` 和业务库由主会话接入；本开发会话未写这些文件。团队队列、GPU、收费 API、网页工作台不在本步。
