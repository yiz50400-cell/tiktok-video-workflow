# 多市场批量商品视频任务设计

## 目标

让员工可以在 Codex 直接提出“这个商品，巴西市场，给我出 24 条”，任务记录同时表达目标市场、当地语言、目标数量、已规划数量和已成片数量，并为每条视频保留不同的广告角度计划。

## 范围

- 支持四个市场：MX、BR、ID、PH。
- 将市场映射到默认口播语言：es-MX、pt-BR、id-ID；菲律宾接受 fil-PH、en-PH 或 taglish-PH，未指定时记录待选，不擅自替用户选择。
- 创建任务时生成目标数量对应的待创作条目；计划数从零开始，由 Codex 依据商品与素材提交实际创意后增加，不用模板凑数。
- 每条计划包含角度、钩子、购买顾虑、证明动作和 CTA 方向；重复角度必须被拒绝或标记，不能以换音乐/变速冒充新视频。
- 前三条为默认代表样片候选，样片确认不等于其余稿件批准；完整批量状态可在任务详情中查看。

## 非目标

- 本次不接入付费 LLM、TTS、视频渲染、平台发布或自动 TikTok 搜索。
- 本次不自动确认色号、价格、折扣、商品卡链接或素材授权。
- 本次不把多条计划写成已经完成的成片。

## 数据约定

任务 payload 新增：

```json
{
  "market": "BR",
  "market_profile": {"name": "巴西", "voiceover_locale": "pt-BR"},
  "count": 24,
  "batch": {
    "status": "WAITING_CONCEPTS",
    "target_count": 24,
    "planned_count": 0,
    "rendered_count": 0,
    "representative_count": 3,
    "items": []
  }
}
```

批量登记由纯本地函数完成，供 API、CLI 和 MCP 复用。实际创意包含 ordinal、angle、hook、buyer_concern、proof_requirement、cta_direction，可选本任务 source_asset_ids；它们是待审创意，不是商品事实已获验证的镜头计划。提交时校验数量范围、目标槽位及重复创意，保留版本记录并重置待审状态。素材容量与七天禁复用在选镜头阶段继续执行，创建目标数量不代表已有足够素材。
