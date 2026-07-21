---
name: skill-equity-placard-watchlist
description: 当需要监控 A 股"谁举牌了、举到第几档、是财务投资还是想抢控制权、谁快要举牌了"时，使用此 skill。从十大股东快照重建持股比例上穿 5%/10%/15%/20%/25%/30% 的权益变动事件，剔除通道账户与股本稀释造成的假举牌。可被复盘 agent 或事件驱动 Alpha 调用。
tags: [quant, build, development, placard, shareholder, event-driven]
---

# 举牌行为监控 BUILD（#17）

## 工具定位

- 工具类型：监控预警 + 事件检出型 BUILD
- 解决问题：把"谁在悄悄买成大股东"这件事量化出来——
  1. **谁举牌了**（上穿 5% 法定披露线）？
  2. **举到第几档**（10/15/20/25/30% 梯度，档位越高越接近控制权诉求）？
  3. **想干什么**（只赚差价 vs 想进董事会甚至抢控制权）？
  4. **谁快要举牌**（已 4.x%，再增持少量即触发披露）？
- 使用对象：盘后复盘 agent / 事件驱动 agent / 人工尽调 / 组合 Alpha（当事件特征）
- **明确不做**：不预测举牌方后续动作、不判断标的价值、不给买卖建议。仅研究/教育示例，**不构成投资建议**。

## 数据现实（重要：清单点名的接口不存在）

任务清单 #17 点名 `get_stock_equity_placard`，但该接口在 **PandaData 最新接口文档（187 方法）中不存在**，
文档里连"举牌 / 权益变动 / 一致行动"字样都没有。因此举牌事件由 **`get_top_holders` 十大股东持股比例快照重建**：
同一股东的持股比例在相邻报告期之间**上穿 5% 及其梯度**即为举牌。

这决定了本 skill 的根本口径：**报告期快照重建，非实时举牌播报**。

## 核心框架

```
十大股东快照(get_top_holders)
   ↓ ① 只取 stock_type='total'   ← 口径陷阱：flow 行的比例是在流通股本上算的
   ↓ ② 按 (票,股东,报告期) 去重，保留最早公告日  ← 一个公告日可携带多个报告期
   ↓ ③ 按 (票,股东) 排序求相邻期持股变动
   ↓ ④ 推断 (票,报告期) 的股本变动系数 k   ← 假阳性陷阱：增发稀释 ≠ 减持
   ↓ ⑤ 实际越线 − 稀释本就会造成的越线 = 交易造成的越线
   ↓ ⑥ 主体分类：通道账户(北向托管/回购专户/国家队) 单独成表，不冒认举牌
   → placard / placard_raise / placard_exit / approaching
```

### 两大假阳性处理（本 skill 的核心价值）

**① `stock_type` 双口径**

`get_top_holders` 同时返回 `total`（十大股东，全部已发行股口径）与 `flow`（十大流通股东，只算流通部分）两套行。
`flow` 行的 `hold_percent_total` 字段名虽同，值却是在**流通股本**上算的。真机实测格力电器持盾安环境 20241231：

| stock_type | hold_percent_total | 公开事实 |
|---|---|---|
| `flow` | 25.38% | ~38% |
| `total` | **38.46%** ✅ | ~38% |

举牌是按**已发行股份**算的，必须只取 `total`。

**② 股本变动导致的被动稀释 / 缩股**

总股本变化时，所有未交易股东的持股比例被**同一系数整体缩放**。真机实测：

| 案例 | 现象 | 真相 |
|---|---|---|
| 301629.SZ @20250324 | 7 个股东比值全是 **0.75000** | 发行 1/3 新股，无人减持 |
| 600358.SH @20260717 | 6 个股东比值全是 **0.4341** | 发行股份购买资产，总股本涨到 2.3 倍 |

处理：按 (票, 报告期) 取比值中位数为 `k`（需 ≥3 个股东且 ≥60% 聚在 ±1% 内才敢认定），
`base_pct = prev_pct × k` 即"完全不交易应有的比例"，**越线判定 = 实际越线 − 被动越线**。

反向同样重要：回购注销使总股本缩小，所有人被动升破 5%——**那不是举牌**。

## 输出（BUILD §11 标准面板）

主键 `(trade_date, build_id="17", target_id, result_type, holder_name, as_of_date)`。
> `as_of_date` 必须进主键：一个公告日可能同时披露多个报告期的持股，漏掉会让不同报告期的事件互相覆盖（实测丢失 12 条）。

| result_type | 含义 |
|---|---|
| `placard_event` | 举牌类事件（首次举牌 / 继续加码 / 减持跌破） |
| `channel_flow` | 通道账户越线（**不算举牌**，仅资金流向参考） |
| `approaching` | 逼近举牌线的观察名单 |
| `placard_summary` | 每次运行的 MARKET 汇总行 |

关键字段：

| 字段 | 说明 |
|---|---|
| trade_date | **公告日**（PIT 基准，回测无前视） |
| as_of_date | 持股报告期截止日（复盘用） |
| prev_pct / pct | 上期 / 本期持股占总股本比例（%） |
| base_pct | 股本变动校正后的基准（"完全不交易应有的比例"） |
| delta_pct / active_delta | 名义变动 / **主动交易变动**（= pct − base_pct） |
| share_change_k / change_kind | 股本变动系数 / `active`（无股本变动）or `mixed`（伴随股本变动） |
| crossed_line / placard_round | 越过的最高梯度线 / 举牌轮次（5%=1、10%=2…） |
| is_control_bid | 是否已越 30% 控制权线 |
| lock_until | 短线交易归入权锁定期截止（公告日 + 6 个月） |
| intent | 意图倾向：战略-控制权 / 战略倾向 / 财务倾向 / 待观察 |
| holder_class / nature | 举牌人 or 通道类 / 自然人·产业资本·金融资本·其他机构 |
| plain_text | 一段人话研判（agent 可直接引用） |

## 输入

`run(input_data, config=None)` 三种输入：

| 形态 | 例 | 说明 |
|---|---|---|
| watchlist | `{"symbols":["601005.SH"],"start":"20240101","end":"20260721"}` | 逐票拉取 |
| scan | `{"mode":"scan","start":"20250101","end":"20260721"}` | 全市场（`symbol=''` 一次拉完，实测 55 万行 / 63 秒） |
| 直连 | `{"panel": <已归一化 DataFrame>}` | 调用方已有数据，跳过拉数（离线可用） |

## 调用方式

```python
from scripts.build import run, maintain_daily, save_parquet
panel = run({"mode": "scan", "start": "20250101", "end": "20260721"})
placards = panel[panel.result_type == "placard_event"]

from scripts.render import render_markdown, render_html
md = render_markdown(panel); html = render_html(panel)     # 学术读数 + 通俗解读

save_parquet(maintain_daily(end="20260721"))               # 生产维护
```

命令行：
```bash
python scripts/build.py --mode scan --start 20250101 --end 20260721 --save --html board.html
python scripts/build.py --symbols 601005.SH --start 20240101 --end 20260721
python scripts/test.py                                     # 全离线自测（无 SDK 也全绿）
```

## Agent 执行规则

1. 调用方已有面板数据 → 用 `run({"panel":...})`，不重复拉数。
2. 每日生产用 `maintain_daily()` + `save_parquet()`，他人读 parquet 不重算。
3. **问答**：读 `database.parquet` 按 `result_type` 拆分，用 `plain_text` 直接回答；深挖读 `result_json`。
4. **禁止把 `channel_flow` 说成举牌**——北向托管账户穿过 5% 没有任何人申报权益变动。
5. 引用结论时必须带边界：**报告期快照口径、非实时播报、非投资建议**。
6. 先 `python scripts/test.py` 全绿；真实数据因配额/无 SDK 自动跳过（不判失败）。

## 术语表（学术 → 人话，交付语言规范）

| 学术术语 | 人话解读 |
|---|---|
| 举牌 | 单一股东持股达到 5%，触发《证券法》强制披露 |
| 举牌梯度 | 5% 之后每增减 5% 需再次披露；档位越高越接近谋求控制权 |
| 控制权线 | 30%——越过须触发要约收购或申请豁免，是谋求控制权的分水岭 |
| 短线交易归入权 | 举牌后 6 个月内反向交易的收益须归公司，短期内难以套现 |
| 财务投资 / 战略投资 | 赚取价差 / 谋求董事席位乃至控制权 |
| 一致行动人 | 约定共同行使表决权的多方，法律上合并计算持股（**本数据源无此字段**） |
| 通道账户 | 北向托管、回购专户等代持类账户，持股越过 5% 不构成举牌 |
| 被动稀释 | 公司增发后总股本增加，未交易股东的持股比例同步下降，不属减持 |
| PIT | 只用当时已公告的信息下判断，不偷看后来才披露的数据 |

## 可被 Alpha 调用

- 是。`run()` 返回标准面板；`event_type` / `active_delta` / `placard_round` / `intent` /
  `is_control_bid` 均可作事件驱动因子特征。
- 调用限制：watchlist 模式需 `symbols`；事件粒度受报告期约束（非日频）。
- 依赖数据：见 `references/api_guide.md`。

## 是否需要生产结果

- 生成 `database.parquet`：是（事件型，盘后统一扫描，多人复用）。
- 更新频率：每日收盘后 `maintain_daily()` 追加（十大股东披露集中在定期报告期，日频跑成本低）。
- 字段结构：见 `../生产产物/SKILL.md`。

## 依赖

- panda_data ≥ 0.0.9（`get_top_holders`）
- pandas、pyarrow（生产 parquet；缺失自动降级 CSV）
- 凭证：`PANDA_USERNAME`/`PANDA_PASSWORD` 或 `~/.pandadata/pandadata.env`（**绝不硬编码**）
- 核心逻辑 `placard.py` 纯逻辑零 IO，`test.py` 全离线可跑（无 panda_data 也全绿）

## 数据边界 / 免责

数据源 PandaData `get_top_holders`。
**假设**：5% 以上股东必然出现在十大股东表；持股比例的相邻期变化可归因为"交易"或"股本变动"两类。
**已知限制**：① 报告期快照口径，报告期之间发生并回撤的举牌看不到，非实时播报；
② 一致行动人无法合并（数据无该字段）；③ 仅十大股东，榜外存在理论盲区；
④ `intent` 为倾向判断而非定论（数据无"举牌目的"字段）；
⑤ 股本变动系数需 ≥3 个股东才敢推断，2 人以下的小样本组合不做校正。
**风险边界**：**仅量化研究与教育示例，不构成投资建议，不承诺收益**；举牌事件不代表标的会涨。
