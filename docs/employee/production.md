# 配音、剪辑与交付

按当前已确认 creative 和内部通过的本地化稿制作。巴西音色池位于 engine/assets/voice-pools/br，字体 engine/assets/fonts/NotoSans.ttf。先校验哈希。

production_config 含 voice_approval、voice_approval_sha256、voice_options、width、height、encoder、packaging、source_continuity_review。关键动作完整，源片内部切点纳入审核；镜头短于2秒逐项说明。逐条锁定音色，重试复用。

本地化审核绑定 creative、中文批准、本地化文件 SHA，reviewer 独立。配音测量真实时长、自然度及失真，字幕同步。技术核验与真人听感、完整播放复核分别保存。

pipeline run 失败只恢复受影响步骤。完成时交付成片、字幕、中文说明、manifest 及证据。商业复用许可待核实时，只交付内部测试，不视为可发布。员工手动上传发布。
