---
name: skill-equity-placard-watchlist-production
description: 当需要读取"举牌行为监控"（#17）的生产结果时，使用此 skill。读取已生成的 Parquet 结果（举牌事件/通道越线/逼近观察/汇总），不重复拉数与重算。
tags: [quant, build, production, placard, shareholder]
---

# 举牌行为监控生产结果（#17）

## 工具定位

- 工具类型：监控预警 + 事件检出型 BUILD 的生产结果
- 服务对象：盘后复盘 agent / 事件驱动 agent / 人工尽调 / 组合 Alpha
- 是否可被 Alpha 调用：是（`event_type`/`active_delta`/`placard_round`/`intent` 等作事件特征）

## 结果文件

- 路径：`database.parquet`
- 格式：Parquet（无 pyarrow 时开发脚本降级 CSV）
- 更新频率：盘后 `maintain_daily()` 追加
- 生成任务：`scripts/build.py`（`--mode scan` 全市场 / `--symbols` 指定票）

## 当前内容（随包样例，真实数据）

- **溯源**：由 `python scripts/build.py --mode scan --start 20250101 --end 20260721 --save`
  从**真实 PandaData** 生成（非合成/非测试桩），可用同一命令重建。
- 规模：**5734 行**，覆盖 **1345 只个股**，公告日范围 `20250107 ~ 20260716`。
- 构成：`approaching 3655` / `placard_event 1781` / `channel_flow 297` / `placard_summary 1`
- 举牌事件明细：首次举牌 **37** 起、继续加码 **188** 起、减持跌破 **1556** 起
- 意图分布：待观察 1372 / 财务倾向 245 / 战略倾向 119 / 战略-控制权 45
- 主体性质：其他机构 623 / 自然人 608 / 产业资本 301 / 金融资本 249

> **为什么减持跌破远多于首次举牌？** 这是 A 股结构性事实而非 bug：十大股东里大量是 pre-IPO 股东、
> 创始人、PE 基金，解禁后逐步减持会反复跌破 10/15/20/25/30% 各档；而买上 5% 的新举牌方本就稀少。

## 主键

`trade_date` + `build_id`(="17") + `target_id` + `result_type` + `holder_name` + `as_of_date`

> `as_of_date` 必须在主键内：一个公告日可能同时披露多个报告期的持股，漏掉会让不同报告期的事件互相覆盖。

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| trade_date | string | **公告日**（PIT 基准，回测按此日可见） |
| as_of_date | string | 持股报告期截止日（复盘口径） |
| build_id / build_name | string | "17" / 举牌行为监控 |
| target_id | string | 股票代码（汇总行为 MARKET） |
| result_type | string | `placard_event` / `channel_flow` / `approaching` / `placard_summary` |
| result_value | string | 核心结果（如 `placard@5%`） |
| holder_name | string | 股东名称 |
| holder_class | string | `举牌人` / `北向托管` / `回购专户` / `国家队` / `结算登记` |
| nature | string | 自然人 / 产业资本 / 金融资本 / 其他机构 |
| event_type | string | `placard` 首次举牌 / `placard_raise` 继续加码 / `placard_exit` 减持跌破 |
| prev_pct / pct | float | 上期 / 本期持股占**总股本**比例（%） |
| base_pct | float | 股本变动校正基准（"完全不交易应有的比例"） |
| delta_pct / active_delta | float | 名义变动 / **主动交易变动**（= pct − base_pct） |
| share_change_k | float | 该 (票,报告期) 的股本变动系数（1.0 = 无变动） |
| change_kind | string | `active`（无股本变动）/ `mixed`（伴随股本变动，已校正） |
| crossed_line | float | 越过的最高梯度线（5/10/15/20/25/30） |
| placard_round | int | 举牌轮次（5%=1、10%=2 …） |
| is_control_bid | bool | 是否已越 30% 控制权线 |
| lock_until | string | 短线交易归入权锁定至（公告日 + 6 个月） |
| intent | string | 战略-控制权 / 战略倾向 / 财务倾向 / 待观察（**倾向判断，非定论**） |
| plain_text | string | 一段人话研判（可直接引用给用户） |
| result_json | string | 完整事件 JSON |
| source_data_date / data_version / update_time | string | 原始数据日期 / 版本 / 生成时间 |

## 读取规则

```python
import pandas as pd, json
df = pd.read_parquet("database.parquet")

# 真实举牌（已剔除通道账户）
placards = df[(df.result_type == "placard_event") & (df.event_type == "placard")]
# 冲着控制权来的
control = df[df.is_control_bid == True]                       # noqa: E712
# 逼近举牌线，下一期可能举手
watch = df[df.result_type == "approaching"]
# 直接引用人话研判
for _, r in placards.iterrows():
    print(r.target_id, r.holder_name, r.plain_text)
# 深挖
detail = json.loads(placards.iloc[0].result_json)
```

## 禁止行为

- **不允许把 `channel_flow` 当举牌播报**——香港中央结算是北向托管账户，穿过 5% 没有任何人申报权益变动。
- 不允许 agent 查询时重新拉数 / 重跑检测。
- 不允许手工修改 Parquet。
- 引用时必须带边界：**报告期快照口径、非实时举牌播报、非投资建议**。
- 不得把 `intent` 当成确定结论——数据无"举牌目的"字段，那只是先验倾向。

## 示例样本

- `sample_placard_dashboard.html` — 暗色看板（举牌/加码优先展示、通道账户单独成表、超量显式声明不静默截断）
