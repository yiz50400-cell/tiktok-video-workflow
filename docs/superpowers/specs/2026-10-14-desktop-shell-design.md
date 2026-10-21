# Windows 桌面交付设计

用户已批准继续自定义界面与 EXE 封装。采用 Electron + React 界面，内置固定 Codex CLI、Python/FFmpeg 引擎，通过 Codex app-server stdio 协议连接。相较系统 WebView，Electron 能固定浏览器环境；相较直接套现有 Codex Desktop，独立客户端可明确控制业务界面、凭据与发行依赖。

## 组件和交付合同

- 独立中文界面：任务列表、商品/市场/数量输入、Codex 对话、中文稿准确版本确认、逐条进度、素材/成片预览及交付目录打开。四市场沿用现有语言合同；每任务数量 1–100。
- Electron 主进程拥有凭据及子进程；renderer 关闭 Node 权限、启用 contextIsolation/sandbox，通过固定 IPC 调用业务 API，不能请求任意 URL、执行任意命令或读取凭据。
- Key 通过 Electron safeStorage 本机加密保存，Windows 使用 OS 数据保护；存储不可用时拒绝保存，不退回明文。读取个人 Codex 配置仅用于用户主动导入，不改原配置。
- Codex 独立 CODEX_HOME 只写 env_key；保留导入时的模型名称。使用 stdio 初始化、thread/start/resume、turn/start、流式通知、turn/interrupt。未知 server request 返回明确不支持；命令/文件权限请求展示并由员工决定，中文文案批准另按准确版本记录。
- 批准端只展示准确中文稿与制作方案，批准提交绑定实际 creative 哈希及真实界面动作来源。哈希变化返回冲突；不新增当地语言人工审批。
- 业务数据、工作文件、Codex 会话与个人设置在用户目录；程序升级不覆盖它们。包中只包含引擎及规则，不含个人 config/auth、历史项目、运行库、Key 或现有素材。
- 包内固定 Python 3.12、工作流依赖、imageio-ffmpeg 和 Codex CLI 0.152.0。可选 LaMa 模型及 GPU 加速保持独立诊断；初次安装不隐式下载模型或付费素材。
- 后端绑定随机本地端口、随机本机 token；桌面自管生命周期。关闭应用清理后端/Codex 树；任务状态与已成功输出可恢复。
- 安装形式为 Windows x64 NSIS Setup.exe，可由老板复制分发，员工首次设自己的中转与工作目录。Windows 安装/启动/完整制作实机验收未完成前标为测试构建。
- 听感、动作连续性、商业授权和最终成片认可沿用既有门禁。制作/中转速度结论以真实测试为准。

## 验证

Python API 临时数据库：中文预览及哈希确认、目录路径越界拒绝、真实任务状态。Node 测试：RPC 分包/交错响应、错误/断线/超时、权限请求、凭据不返回 renderer、线程绑定及恢复。实际 app-server 握手/短回答/本地工具联调。界面构建、浏览器操作及截图检查。发行目录白名单及凭据扫描，NSIS 构建实际执行。Mac 检查与 Windows 实机验收分别记录。
