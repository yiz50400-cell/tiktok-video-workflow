# ChatCut 包装能力核对

2026-10-15；本机 macOS，ChatCut Desktop 0.6.13。范围为能力、工具入口及参数校验，没有向正式六条视频新增包装。

## 结论

ChatCut具备动效标题、卖点标签、透明叠加、局部放大、缩放、转场和音效等包装能力，Codex连接的Desktop工具暴露了对应调用入口。当前完成能力核对，不等于全部效果已在成片中逐个渲染验收，也不等于批量包装稳定性通过。

## 本机证据

- 已连接项目：b4951471-b793-4c07-8526-3ecff1eee678。
- browse_library实际返回：动效211、LUT 2、缩放6、视觉效果17、音频效果23、音效36、转场16。转场包含12个视觉转场和4个音频转场。
- 可读且有apply调用说明的模板：Dark Tech — Callout Tag（8744f05e-4976-4430-a14e-85a167301c14）、Lower Third 01 - Pill Badge（08aeec62-dffe-4973-b1da-b9bddd492d63）、Hand-Drawn List Overlay（3554d937-05c5-4ffb-835d-aa8db1a48ace）。Callout模板源画布1920×1080；竖片使用时仍需调整并预览。
- 对第六条现有片段执行validateOnly：Magnifying Glass、Slow Push、8帧Cross Dissolve，均通过参数与目标验证。验证未提交效果，未执行这三个效果的实际渲染。随后inspect_item复查目标片段无新增效果或转场。
- Tiny Bubble Pop返回了明确的原生音频放置调用方式。本次未把它加入工程。原有小标题、透明字幕图层与轻提示音已在此前六条中导出。
- 搜索sticker只返回两个贴纸配套音效，没有发现独立贴纸分类；这不能证明产品绝对没有贴纸，仅表示当前可调用入口未核实。
- 导入类型支持PNG、GIF、SVG；PNG透明图层已在本批验证，GIF/SVG动画及具体素材需另做真实导出检查。

## 能力边界

普通箭头、圈选、角标等可走外部透明素材或自定义Motion Graphic路径；本次未生成或测试具体形态。不能将其描述为已经连通剪映/CapCut完整热门贴纸库。复杂自动跟踪、全部模板竖版适配及批量包装耗时没有本次实测证据。

## 官方资料

- https://chatcut.io/docs/library
- https://chatcut.io/docs/motion-graphics
- https://chatcut.io/docs/ai-effects
- https://chatcut.io/docs/supported-formats
- https://chatcut.io/docs/music-and-sound-effects

上述页面均于本日打开核对。判断以当前本机接口与官方资料交叉核验，未将模板条目数量当成可直接用于本批的效果数量。

## 同日新增：第2条真实包装样片

用户授权单独任选一条制作后，复制第2条为独立时间线a59b2c80-c98c-46c3-9145-9f04c4b1794c。实际导入2个自定义Remotion动效源，放置3处可编辑动态标题、1处SVG路径刷头指示，并使用3次原生Tiny Bubble Pop音效。原生1080p导出任务dbba3565-9a7a-4497-82cd-fa148f4d5e77已完成。

成片22.8秒、1080×1920、30fps、684帧，完整解码通过；12段字幕、5段视频与旁白时间线核对一致，已检查实际导出画面的动效进入与退出。详细证据在projects/sky-tubes/batch-mix6/chatcut/packaging-demo-02。该结果将上述“具体形态未渲染”的范围收窄：本样片的动态标题、透明SVG指示及音效已经实际导出；其余效果、完整贴纸库、批量包装稳定性及Windows运行仍不据此宣告通过。

