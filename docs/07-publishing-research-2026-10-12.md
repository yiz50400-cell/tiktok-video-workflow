# 发布接入调研

日期：2026-10-12。当前为公开文档调研；未登录业务账号、上传视频、创建平台草稿或发布。

## 用户最新方向

> **范围更新（2026-10-13）：第07步发布接入暂不实施。下方接口、紫鸟和竞品资料只保留为历史调研；当前工作流到成片交付结束，员工自行手动发布。**

- 员工通过 Codex 操作，沿用各员工本机素材库，不新增工作台或中央队列。
- 用户自己运营账号，已有商品橱窗；具体店铺绑定角色、注册主体、发布账号和授权尚未核验。
- 用户允许简化：自动准备并送达待发布入口，员工手动挂商品和正式发布。希望能保存在草稿箱。
- 发布模块另开 07 会话，主会话负责整体协调。暂不继续批量制作。

## 官方接口核实

### TikTok Shop 带货视频接口

来源：https://partner.tiktokshop.com/docv2/page/post-shoppable-video-202603

公开正文确认：POST /affiliate_creator/202603/videos，需要 creator.video.write 和创作者 access token；video_info 接收上传得到的 file_id、标题、封面及可选 music_id；product_link_info 要求 product_id 和商品锚点标题。成功提交后必须通过状态接口等待 PROCESSING / SUCCESS / FAIL，提交成功不是发布成功。所读请求字段没有定时发布时间或草稿保存参数。

页面“查看应用类别”，以 China 注册地区为示例，逐一查询：

| 目标市场 | 页面结果 |
| --- | --- |
| Brazil | App developer / Customer Engagement / Shoppable Content；Seller inhouse developer / Seller inhouse developer / TikTok Shop Seller |
| Mexico | 同上 |
| Indonesia | 所选注册地区和目标市场下暂无可用 API 类目 |
| Philippines | 所选注册地区和目标市场下暂无可用 API 类目 |

这是文档查询结果，不是用户公司注册地区确认、应用审核通过或具体账号调用验证；不能推广为其他主体或所有账号的可用性。Get Creator Profile 202508 和 Get Shop Products 202509 的菜单链接打开后提示没有这个文件，不能声称读取成功。

概述：https://partner.tiktokshop.com/docv2/page/affiliate-creator-api-overview

创作者授权独立于 TikTok for Business / TikTok for Developers；应用可用 scope 与类别、注册地区及目标市场有关。旧概述中的关注数和地区示例不能代替当前账号资格核验。

### 通用 TikTok 发布与上传

来源：
- https://developers.tiktok.com/doc/content-sharing-guidelines
- https://developers.tiktok.com/doc/content-posting-api-get-started
- https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- https://developers.tiktok.com/doc/content-posting-api-reference-upload-video

Direct Post 的公开指南明确把仅用于自己或团队管理账号上传内容的内部工具列为不接受的目标用途。此约束不能自动套到 TikTok Shop 接口。未审核应用只能私密发布等限制，不能当作公开生产方案。

Upload API 使用 video.upload，上传后通知用户到 TikTok App 的收件箱继续编辑和发布。它不是网页草稿箱，也不是我们可后台定时发布的草稿。官方文档规定待处理分享数量上限；实施前重新核验当前限制、应用准入和账号授权。该路线更接近用户允许的手动挂商品模式，但仍须实测该市场和账号在这个编辑入口是否能添加商品、能否保存草稿。没有验证前不承诺。

## 竞品结论

| 路线 | 已核实能力 | 对本项目的判断 |
| --- | --- | --- |
| Buffer | 自动排程与通知发布；API / MCP 可供对话工具接入 | 可借鉴任务、排程、失败回读。所读文档没有证实视频商品挂载 |
| Later | 自动发布、排程、团队审批、主页链接 | 主页链接不能当作视频小黄车支持 |
| Metricool | 自动发布与通知模式、团队审批、多平台排程 | 同样没有证实本项目所需商品挂载及账号草稿保存 |
| 账号原生页面＋紫鸟 | 紫鸟支持浏览器环境启动和控制 | 商品选择、草稿、发布是 TikTok 具体页面能力，需要真实入口实测；不是紫鸟直接提供业务 API |

来源：
- https://support.buffer.com/articles/using-tiktok-with-buffer-oGEroY9Of2
- https://buffer.com/api
- https://later.com/tiktok-scheduler/
- https://metricool.com/schedule-tiktok-videos/

不需要为了普通排程先购买第三方服务。用户最新允许手动挂商品，第一期优先验证待编辑送达与草稿能力，自动挂商品作为后续可选接入。

## 最小方案与可见验收

Codex 接收成片、账号、市场 → 生成发布准备包和中文说明 → 选择该账号已验证的送达方式 → 核对平台是否实际收到 → 员工挂商品并发布 → 获授权后回读结果。

1. 先提供本地发布准备包与中文预览，复用已完成成片，不重复制作。
2. 指定账号和上传授权后，验证单条真实视频送达。分别记录“本地准备完成”“收件箱待编辑送达”“平台草稿保存确认”，不混淆状态。
3. 若确有原生保存草稿能力，交付可见草稿；若只能送收件箱，则展示实际入口差别，由用户确定是否接受。登录验证与人工商品选择不做绕过。
4. 手动挂商品和发布由员工完成；不会因中文稿批准或成片认可而获得账号上传、授权应用或正式发布权限。

同账号由一个负责人/执行机处理，本机持久化任务状态；超时先查是否已送达再重试，避免重复。当前不实现跨员工共享协调。定时属于后续可选项，不把收件箱送达说成自动定时发布。
