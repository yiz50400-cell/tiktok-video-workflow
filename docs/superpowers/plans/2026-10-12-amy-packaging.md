# Amy 15秒包装制作计划

> **For agentic workers:** Use subagent-driven development for isolated voice selection and independent localization review; media assembly stays in this session.

**Goal:** 按用户确认的五句中文和五个时间窗交付第一条竖屏15秒样片。
**Architecture:** 复用规范H264源，保存新稿批准与内部本地化证据；生成五句实测配音，按动作窗前置衔接；透明文字层和自制轻音效进入同一次最终编码。各阶段按输入哈希缓存，仅重试失败步骤。
**Tech Stack:** Python、edge-tts、FFmpeg、Pillow、pytest。
**Spec:** AGENTS.md及本轮中文确认，creative.json保存逐字内容。

## Constraints

- 1080×1920、15秒、原速镜头、同一人双颊试用。
- 声音限BR已认可池；单条锁定参数；中文确认和内部本地化审核分开。
- 白字粉色强调；三处轻音效；无额外音乐；动作可见。
- 首条交付后等待方向验收，另两条本轮不制作。

## Execution

- [x] 保存creative/zh-approval；独立复核译文和可见文字，绑定准确字节。
- [x] 修复声音选择轮换、跨任务市场池复用及参数锁定，新增针对性测试并运行旧生产回归。
- [x] 选择第一侧点涂17秒附近与刷开22秒附近、第二侧38秒与40秒附近的完整动作；组合固定15秒时间轴。
- [x] 新建可重复制作入口，生成已锁定音色的配音、真实PCM时长/词时序，前置安排避免居中长停顿。
- [x] 将白字粉色重点PNG、三处自制音效绑定进制作输入和最终输出；SRT随实测词边界切短句。
- [x] 完整解码/时长/音量/黑帧/冻结检查，抽帧与切点独立复核；检查真实缓存复跑，登记最终版本台账。
- [x] 更新当前批状态和交付说明，复制桌面版本并在对话展示可播放MP4。
