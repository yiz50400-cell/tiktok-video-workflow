# 第07步发布准备模块（暂不纳入当前工作流）

> **交付范围说明（2026-10-17）**：第07步发布接入不在当前交付范围内，本工作流不提供任何账号登录、上传、草稿、挂商品或发布能力。下文为历史调研记录，店铺环境名、storeId、IP 与本机路径已替换为占位符；不得据此操作任何账号。

2026-10-13（Asia/Shanghai）：用户决定暂不做第07步发布接入。当前工作流在成片、字幕、中文说明和本机交付包完成后结束；紫鸟启动、TikTok Studio打开、平台上传、收件箱送达、草稿保存、商品挂载和正式发布均不属于当前流程，全部由员工自行手动完成。本文件中本地包脚本仍可用于本机文件交付；其余账号与平台实测内容仅作为历史调研记录，不是当前执行入口。旧包中的“中文发布预览”和上传待办保留原始证据，新生成的包只把质量与商用许可列为交付前待核实项。

## 当前范围决定

- Codex 负责：素材采集、参考结构筛选、本地化、配音、剪辑、包装、质量检查和成片交付。
- 员工负责：自行打开账号环境，登录 TikTok，上传视频，挂商品并发布。
- 当前不实现：紫鸟接入、TikTok Studio 自动上传、平台草稿、收件箱送达、自动挂商品、发布状态回读和自动发布。
- 后续若重新启动发布模块，必须由用户明确提出；在此之前不得根据本文件自动操作账号或上传文件。

## 账号环境约束（最新决定）

用户选择紫鸟是为了保留既有账号网络与会话安排。目标绑定“<目标店铺环境名>”、storeId `<storeId>`，市场BR；后续复用该账号原紫鸟窗口、配置、代理和会话。视频账号handle尚未核实，店铺环境名不能替代视频账号身份。

运营账号不转到普通Chrome、Codex内置浏览器、另一执行机或网络未核实的手机；普通浏览器只用于公共文档。不得为自动化更换代理、关闭隔离、迁移/导出Cookie或自动回退本机直连。手机方案须先确认用户已安排该账号设备与网络；网页不能接续挂商品时先汇报，由用户决定。

紫鸟品牌、独立窗口及配置记录均不是IP隔离证据，不承诺防封号。只读核实目标身份和网络配置；必要的实际出口检查须先取得明确授权，只在该原环境内执行。环境不能核实或代理异常时停止该账号外部操作。本轮没有新增上传或正式发布授权，员工手动挂商品与正式发布。

## 历史调研：三种入口

| 入口 | 实际含义 | 当前能力 |
| --- | --- | --- |
| 本地发布准备包 | 本机成片、准确文案、封面、中文预览与证据记录 | 已实现并实跑 |
| TikTok App收件箱待编辑 | Upload API送视频，账号收到通知后员工进入App编辑 | 官方流程已核实，应用/账号与商品挂载待实测 |
| 平台原生草稿 | 员工在真实账号编辑流程保存，能在对应草稿入口找到并重开 | 尚未验证本项目目标账号与入口 |

当前只使用本地包交付。本表的平台入口调研留存备查，不能据此开展账号或上传操作。

## 官方补充复核

沿用 [2026-10-12调研](../07-publishing-research-2026-10-12.md) 的Shop接口、竞品与Direct Post结论，未重复全量调研。

2026-10-13通过Codex后台浏览器实际读到官方正文（页面显示2026-08-04更新）：

- [Get Started - Upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content)：须注册应用、添加Content Posting API、应用获批video.upload、目标用户授予该scope并取得对应token/open ID。标题虽写“Upload draft to TikTok”，正文要求用户点击收件箱通知继续编辑和发布；这不能证明已保存到账号原生草稿列表。
- [Video Upload](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video)：POST `/v2/post/publish/inbox/video/init/`；每个user access token每分钟6次请求；24小时内最多5个pending shares。FILE_UPLOAD需按返回URL实际PUT，上传URL有效1小时；publish_id用于查询异步状态。
- 该视频Upload初始化请求仅有source_info，未提供视频标题、标签、封面、product_id或保存草稿参数。准确文案/封面随本地包保留，不能承诺通过此API自动预填。照片接口支持标题/描述，不可套用到视频接口。
- [原生编辑/发布帮助入口](https://support.tiktok.com/en/using-tiktok/creating-videos/editing-posting-and-deleting) 跳至TikTok帮助页后显示“出错了 稍后重试”。此次未读取草稿正文，不给出新结论；App/网页/Studio草稿是否可用、是否能挂商品、设备间是否可见，留待具体账号实测。

普通Upload准入有应用获批及用户授权要求。当前文档不足以确认纯内部员工工具一定获批或所有市场通用；不能用Direct Post私密测试限制或用途规则替代Upload的真实审核结论。没有创建开发应用或申请权限。

## 独立执行入口

```sh
.venv/bin/python scripts/prepare_publish.py \
  projects/sheglam-color-bloom/publishing-dev/request-br-v0001.json \
  --output projects/sheglam-color-bloom/publishing-dev/br-local-v0001
```

使用脚本绝对路径可从任意cwd调用，`--workspace`默认项目根。请求JSON中的相对证据路径按workspace解析；输出目录按调用cwd解析。来源不修改，准备包复用视频原字节，封面另取原帧。现有Python、imageio_ffmpeg足够，没有新增主依赖。

Python接口：`workflow.publishing.prepare_publish(request_path, output_path, *, workspace) -> dict`。返回中文预览概要、绝对产物路径、独立平台状态和待补项；CLI不输出外语全文。请求格式见实际 request-br-v0001.json（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/request-br-v0001.json`）。

必需字段：schema_version=1、market、locale、product_name_zh、execution、creative、zh_approval、localization、localization_audit、qa、title_refs、cover_seconds。可选hashtag_refs、account、product_id/product_market；delivery_method仅支持local_package。其他市场沿用项目market_profile语言规则，本期真实样例只验证BR。

title_refs每项为`{line:1, field:"visible_text"}`或text，引用内部本地化完整句及对应中文整句。hashtag_refs只支持visible_text中的连续零基tokens，例如 `[2,3]`；去空格/音调符号形成标签，不增加新卖点。模块提供确定性选取和核验，Codex负责按商品与创意选择引用，不重新生成外语翻译。增加新标题、标签含义或卖点需先中文确认及独立本地化审核，本入口目前拒绝任意新文案。

当前样例中文标题为“Color Bloom 液体腮红｜你选哪一边？”。内部稿使用原已独立审核的两条可见文字，标签取商品名与液体腮红词组。记录是“中文稿用户批准”“独立内部审核继承”“外语人工审批免展示”；不宣称新标题单独经用户审批，也不伪造新一轮独立审稿或母语人工审核。

## 产物和恢复

- `video.mp4`：既有成片的字节副本。
- `subtitle.srt`：现有独立字幕，按执行清单核验。
- `cover.jpg`：所选秒数的原帧缩略图，用于员工在实际编辑入口选封面；平台封面尚未应用。
- `中文发布预览.md`：中文标题、标签依据、样片/封面说明、商品待选、入口区别与待补项。
- `internal/publish-copy.json`、`internal/caption.txt`：准确当地语言标题/标签/全文及审核继承映射，供制作接入使用。
- `internal/evidence/`、`manifest.json`、`receipt.json`：中文批准、本地化审核、执行/QA快照、文件SHA-256与请求指纹。

准备前核验中文批准版本、本地化版本/市场/语言、独立审核、执行清单绑定、QA成片/执行哈希和完整解码记录，独立字幕也需与当前清单相符。准备期间输入变化则停止。技术QA不转成用户最终验收、素材商用批准或账号授权；实际样片保留final acceptance pending和commercial license unverified。

完整包原子写入新目录；同机目录锁防止两个执行进程重复写。重跑同请求校验所有产物后直接复用，输入变化或包损坏要求新编号，不覆盖历史。执行失败写旁路`*.prepare-failure.json`并清理临时包；失败记录作为历史保留，恢复成功以包内manifest/receipt为准。突然断电留下锁时，先核对锁PID与实际进程再人工清理，不能盲重试。没有跨机锁、中央队列或多员工共享协调。

平台状态分别为upload、inbox、draft、publication；当前是not_attempted/not_verified，publish_id、content_id、url均为空。账号和商品ID未知就保持null；提供ID时需同一市场，但仍只是待核对候选，不能自动标记已挂载。

## 已执行验证

20项聚焦测试通过，含真实临时MP4打包、原字节一致、封面提取、中文CLI、过期批准/审核/QA拒绝、语言不符、新文案拒绝、SRT改动拒绝、输入版本冲突、损坏检查、同机锁、失败重试和准备期间请求变化。合并API/CLI/MCP兼容检查共38项通过（1条现有Starlette/AnyIO弃用提示）。测试全部使用临时目录，没有共享业务库操作。

真实BR包位于 br-local-v0001（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/br-local-v0001/中文发布预览.md`），复用07简报指定的efficiency/production-benchmark-v0002样片。视频17.4秒、1080×1920，包内MP4与原片SHA均为`c564b364787a7c7a45e916ba515e5cb061e878b7540e80addaa169994101b703`；13个产物哈希匹配，另跑完整解码通过。封面0.8秒已实际查看：两支产品与涂抹头清楚，原有商品包装文字保留。准备模块实测0.1389秒，只代表已有成片本地复制/封面/记录，不代表新制作、平台上传或批次产能。重跑返回reused=true。

这是保留原字节的历史包，早于 Amy v0003 桌面交付包；不能把它当作最新成片。当前范围与最新文件验收见 [本机工作流验收收尾](../2026-10-13-本机工作流验收收尾.md)。

证据见 verification-v0001.json（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/verification-v0001.json`）。本期未重做采集、去水印或制作。

## 历史共享核心接入建议（暂停）

本会话只新增专属模块、脚本、测试与publishing-dev产物，共享api/cli/store/mcp_server/media、主依赖和.runtime业务库未由本会话修改。

建议主会话新增“prepare publication”调用，内部接本函数，输入为任务当前制作证据和Codex选定文字引用。员工继续通过Codex说商品、市场、数量；不要要求员工手写JSON。结果将preview/video/cover绝对路径交给Codex展示，内部caption供后续适配器使用。

共享记录分别登记LOCAL_PREPARED、UPLOAD_SUBMITTED、INBOX_DELIVERED、PLATFORM_DRAFT_CONFIRMED及PUBLISHED_VERIFIED，并保存适配器类型、目标账号/市场、视频SHA、request/publish/content ID及真实证据。提交成功、媒体PUT完成、收件箱送达不能越级写草稿或发布完成。超时先查真实状态后决定重试。实现外部适配器时需按账号授权单独验证；本地准备包不包含可用token或平台调用器。

## 历史账号接入计划（暂停）

已选BR环境“<目标店铺环境名>”。下一步先确认其现有网络安排、原窗口和实际视频账号身份，再检查原生编辑入口。单条上传仍须明确账号、文件及上传授权；应用新scope授权由用户完成。素材商业复用权和具体成片最终验收仍按现有记录确认。单条实测依次展示真实待编辑送达、草稿保存记录、员工挂商品/发布后的真实结果；正式发布由员工操作。

## 2026-10-13 紫鸟CLI只读接续

用户最新偏好：优先复用已经接过的紫鸟CLI，按方便的路线推进，申请/授权繁琐的路线暂不做。员工手动挂商品和正式发布。先读本机CLI/技能与实际能力，不安装、初始化、关闭浏览器、改代理或申请TikTok API；真实上传须另有明确账号、文件与上传授权。

实际发现 `<本机ziniao CLI路径>` 原生arm64命令及 `<本机紫鸟App>`。读取帮助确认命令族为browser/auth/fix/start/shutdown；browser提供list/start/close，start的help列出normal/agent模式。这只证明存在agent启动选项，没有证明页面点击、截图、填文案或上传已完成。本轮未启动环境。

常用PATH及现有本地安装位置未找到 `ziniao-cli`、`zn-open-eco`；默认 `~/.ziniao-cli` 不存在。Codex/.agents/.claude技能目录中只找到旧 `ziniao-webdriver-doc`，没有适用的已安装CLI技能；没有将旧WebDriver作为必选路线。未读取任何API Key、密码、凭据库或浏览器账号缓存。

只读获取npm `@ziniao-agent/cli` README，注册表当前版本0.1.6，不能当作本机安装版本。README区分生命周期命令和agent tool：browser生命周期不提供页面点击/输入/导航/截图；已有CLI的真实agent tool能力仍需从本机命令发现。文档说明本地App须已打开，凭据来自既有ziniao-cli配置。本轮不运行doctor/config init，因为相应命令未找到，亦不安装或迁移配置。

实际命令 `ziniao browser list --keyword TikTok --limit 20 --json` 退出1，返回 `ok:false` 和 `Ziniao user is not logged in`。应用表面只读清单显示ziniao App未运行。没有取得账号候选；这不等于账号不存在，也不等于此前其他执行机的接入失效。未查询无关店铺业务数据。

当前无法检查指定TikTok网页上传、保存草稿、重开或添加商品，均记录not checked。没有猜测网页草稿同步手机，没有用私密发布替代草稿，也没有将打开环境视为上传完成。

该阶段所需为接通既有CLI并选择环境；登录后检查结果见下节。若查看上传后的编辑/草稿需真实文件，届时针对具体账号和一条文件请求上传授权。后续设备及网络遵循本页最新账号环境约束。

本轮证据：ziniao-readonly-2026-10-13.json（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-readonly-2026-10-13.json`）。没有重做发布包或修改共享核心。

### 登录后选定巴西环境的实际检查

用户说明已登录，选择巴西市场；只读CLI筛出两个环境“<目标店铺环境名>”和“<另一店铺环境名>”。用户明确选择前者，storeId=`<storeId>`。同机调用 `ziniao browser start --id <storeId> --mode agent --json` 返回成功。紫鸟启动过程中自动下载并解压所需内核；没有人工重新安装CLI、初始化凭据、申请权限或关闭已有浏览器。启动输出没有提供可调用的页面agent tool端点，因此页面检查使用cua_repl原生辅助功能，不能把agent启动模式写成页面自动上传能力已接通。

目标环境原有页是巴西卖家中心登录入口，曾显示验证码；本会话没有解验证码或读取密码。随后页面路径变为卖家中心homepage。本会话没有读取订单、财务、商品等店铺业务数据，也没有将卖家中心登录视为TikTok视频账号登录。

在该环境单独新开标签访问 `https://www.tiktok.com/`，紫鸟扩展显示访问拦截页，提示网页未予显示，需要同意免责声明、申请加白。没有同意声明、申请加白、改代理、尝试绕过或用其他域名变体规避。因此TikTok视频账号、上传编辑页、草稿保存/重开及商品添加均未核验。环境启动成功与TikTok业务入口可用分开记录；没有上传任何视频。

后续在用户既有紫鸟环境与网络安排内解决实际入口可用性；目前没有实际送达、挂载或草稿证据。

证据：目标环境检查（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-br-target-check-2026-10-13.json`）及实际访问拦截截图（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-br-access-blocked.jpg`）。当时目标窗口保留；当前运行状态见下节。历史JSON中的手机建议已由最新账号环境约束取代，保留原始检查历史。没有本地包重建或共享核心改动。

### 最新网络与运行状态只读复核

CLI仍连通。`ziniao browser list --keyword 巴西 --limit 20 --json` 返回已选环境及另一个巴西环境，两者的登记IP均为 `<登记IP>`。紫鸟工作台也显示该IP，并显示其地区与线路标记。CLI与界面显示一致，只证明存在该网络配置记录，不能证明请求实际经由该出口，不能证明两账号拥有不同出口，也不能判断共用是否符合用户原安排。未读取代理凭据、Cookie或密码；未访问外部IP检测服务。

原目标窗口辅助功能读取超时；应用清单中没有运行的ziniaobrowser，工作台目标环境按钮显示“启动”。因此本次没有可复用的运行中目标窗口，未重启、创建替代环境或执行账号外部操作。无法确认关闭原因；本会话没有关闭浏览器。

当前最小缺项：确认两个巴西环境登记同一IP是否为用户既有安排，并由用户恢复原目标窗口。需要验证出口时另行明确授权，不能用本机shell直连检测代替目标环境出口。TikTok官网此前的紫鸟访问拦截仍是另一独立待解决项；没有申请加白。

证据：环境与网络复核（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-br-environment-check-2026-10-13.json`）。

### TikTok Studio 直接入口复核（2026-10-13 14:26，Asia/Shanghai）

用户补充曾在紫鸟直接打开 TikTok／TikTok Studio 发布界面。主会话沿用已选“<目标店铺环境名>”（storeId=`<storeId>`），通过既有原生 CLI 启动原环境，在独立标签访问 `https://www.tiktok.com/tiktokstudio/upload`。页面正常进入 TikTok 葡语登录页，登录 URL 的 `redirect_url` 指向上述上传入口；紫鸟扩展显示“可以访问此网站”。本次没有出现此前首页访问的加白拦截，没有更改代理、扩展或网络配置，没有接受免责声明或申请加白。

已核实的是 Studio 入口请求可到达登录页，尚未核实登录后的上传编辑器。此前首页拦截的历史证据仍保留，其原因及本次差异尚未定位，不能由此判断整个紫鸟无法发布，也不能认定所有页面均已可访问。目标视频账号尚未登录和核对；卖家中心会话不代替视频账号会话。草稿保存、重开、挂商品与正式发布均未验证，实际出口隔离也未验证。接下来在同一原环境登录目标视频账号后继续页面检查；本次没有上传任何文件。

证据：Studio 入口检查（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-br-studio-entry-check-2026-10-13.json`）、实际登录页截图（本机证据，未随仓库：`projects/sheglam-color-bloom/publishing-dev/ziniao-br-studio-login-2026-10-13.png`）。本次检查更新原环境运行状态；上节“未运行”是此前检查时点的状态。
