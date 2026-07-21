---
name: skill-equity-placard-watchlist
description: 举牌行为监控——侦测 A 股股东持股比例上穿 5%/10%/15%/20%/25%/30% 法定披露梯度的权益变动事件，含举牌梯度、意图倾向（财务 vs 战略）、6 个月锁定期、逼近举牌线观察名单。剔除通道账户与股本稀释造成的假举牌。BUILD 型 skill，可被复盘 agent 或事件驱动 Alpha 调用。
tags: [quant, build, placard, shareholder, event-driven, corporate-action]
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-equity-placard-watchlist
  repository_url: https://github.com/quantskills/skill-equity-placard-watchlist
  project_type: skill
  collection: corporate-action
  license: GPL-3.0-only
  status: community-project
---

# 举牌行为监控（#17）

> **项目状态：Community Project（社区项目）。** 本项目由社区成员创建，**未经 QuantSkills 官方审核、认证、验证或背书**，
> 也非生产可用认证项目。名称中的 `quantskills/` 仅表示托管组织，不代表任何官方身份。

> **一句话**：有人买你家股票买过 5% 了，法律逼他公开举手说"我来了"——本工具从十大股东快照重建这些
> "举手"事件，并回答**谁在举、举到第几档、是想赚差价还是想进董事会**。
> **报告期快照口径，非实时举牌播报；不构成投资建议。**

## 这个工具做什么

《证券法》规定：投资者持有上市公司**已发行股份 5%** 时须公告（即"举牌"），此后每增减 5% 再公告一次。
本 skill 跟踪每个股东的持股比例时间序列，检出**上穿 / 跌破**这些法定梯度线的事件：

- **首次举牌**（上穿 5%）→ 有新玩家进场了
- **继续加码**（上穿 10/15/20/25/30%）→ 举得越高越像来真的；30% 是要约收购/控制权分水岭
- **减持跌破** → 此前的举牌头寸在退出
- **逼近观察** → 已到 4.x%，再买一点就得举手

每起事件配**意图倾向**（财务 vs 战略）、**6 个月短线交易归入权锁定期**、以及一段人话研判。

## 两个必须知道的口径陷阱（本 skill 的核心价值）

| 陷阱 | 后果 | 处理 |
|---|---|---|
| **`stock_type` 双口径** | `flow` 行的 `hold_percent_total` 是在**流通股本**上算的。实测格力持盾安环境：`flow` 读 25.38%、`total` 读 38.46%（公开事实约 38%）。用错行整个举牌梯度全错 | 只取 `stock_type='total'` |
| **股本变动被动稀释** | 公司增发新股，蛋糕变大，谁也没卖但人人占比下降。实测 301629.SZ 七个股东比值全是 0.75000、600358.SH 六个股东全是 0.4341——不处理会**全部误报成"减持跌破举牌线"** | 按 (票,报告期) 推断股本变动系数 k，只认**交易造成的**越线 |

真机全市场实测：这两项处理共剔除 **17.9% 的假事件**。

## 快速使用

```bash
pip install --upgrade panda_data pyarrow
export PANDA_USERNAME=<手机号>; export PANDA_PASSWORD=<密码>   # 或 ~/.pandadata/pandadata.env

# 全市场扫描 + 落盘 + 出看板
python 开发产物/scripts/build.py --mode scan --start 20250101 --end 20260721 --save --html board.html
# 单票/多票
python 开发产物/scripts/build.py --symbols 601005.SH 002011.SZ --start 20240101 --end 20260721
# 全离线自测（无 panda_data 也全绿）
python 开发产物/scripts/test.py
```

- 详细文档：[开发产物/SKILL.md](开发产物/SKILL.md)
- 数据接口与真机实测：[开发产物/references/api_guide.md](开发产物/references/api_guide.md)
- 质量证据（假阳性复现与修复）：[开发产物/references/quality_evidence.md](开发产物/references/quality_evidence.md)
- 生产结果读取：[生产产物/SKILL.md](生产产物/SKILL.md)

## 边界与免责

数据源 PandaData `get_top_holders`（十大股东快照）。

**已知限制（必须如实转达）**：
1. **报告期快照口径**——只能看到报告期截面，**报告期之间发生并回撤的举牌看不到**；非实时播报。
2. **一致行动人无法合并**——数据无该字段，多个关联方各持 4% 合计超 5% 的情形检测不到。
3. **仅十大股东**——榜外持股虽罕见（5% 几乎必进前十），但理论上存在盲区。
4. **意图为倾向判断**——数据无"举牌目的"字段，`intent` 仅依据主体性质/持股高度/增持力度给先验，非定论。

**Community Project，未经 QuantSkills 官方审核/认证/背书。仅量化研究与教育示例，不构成投资建议，不承诺收益。**
