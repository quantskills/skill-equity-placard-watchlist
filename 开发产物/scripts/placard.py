"""举牌事件识别核心逻辑（纯逻辑、零 IO、零联网 —— 全部可离线测试）。

学术 → 通俗对照（交付语言规范：术语必配人话）：

| 术语 | 人话 |
|---|---|
| 举牌 | 有人买你家股票买过 5% 了，法律逼他公开举手说"我来了" |
| 举牌梯度 | 5% 之后每再买满 5% 就得再举一次手；举得越高越像来真的 |
| 权益变动 | 谁手上这家公司的股份变多变少了 |
| 一致行动人 | 几个人说好一起买，法律上算一个人（本数据源无此字段，见已知限制） |
| 短线交易归入权 | 举牌后 6 个月内反手卖，赚的钱要还给公司——所以举牌方短期内多半跑不掉 |
| 财务投资 vs 战略投资 | 只想赚差价 vs 想进董事会甚至抢控制权 |
| 通道账户 | 北向托管、回购专户这类"代人保管"的账户，穿过 5% 不算举牌 |
| PIT | 只用当时已公告的信息下判断，不偷看后来才披露的数据 |
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

GLOSSARY: dict[str, str] = {
    "举牌": "有人买你家股票买过 5% 了，法律逼他公开举手说'我来了'",
    "举牌梯度": "5% 之后每再买满 5% 就得再举一次手；举得越高越像来真的",
    "举牌线": "5%——《证券法》规定的强制披露线",
    "控制权线": "30%——越过要么触发要约收购，要么走豁免，是抢控制权的分水岭",
    "锁定期": "举牌后 6 个月内反手卖，赚的要还给公司，所以短期内多半跑不掉",
    "财务投资": "只想赚差价，涨了就走",
    "战略投资": "想进董事会、甚至抢控制权，会一路加",
    "通道账户": "北向托管、回购专户这类代人保管的账户，穿过 5% 不算举牌",
    "增持强度": "这一次比上期多买了多少个百分点",
    "PIT": "只用当时已公告的信息下判断，不偷看后来才披露的数据",
}

# ============================================================================
# 参数（集中管理，便于整定）
# ============================================================================
DEFAULT_CONFIG: dict[str, Any] = {
    # 法定披露梯度：首次 5%，之后每 5% 再披露一次；30% 是要约收购/控制权分水岭
    "lines": [5, 10, 15, 20, 25, 30],
    "placard_line": 5.0,          # 举牌线
    "control_line": 30.0,         # 控制权线
    "approach_band": 1.0,         # 逼近预警：距下一条线 ≤1 个百分点即列入观察
    "lock_months": 6,             # 短线交易归入权锁定期（月）
    "min_pct_move": 0.01,         # 小于此变动视为噪声（小数漂移）
    "strategic_jump": 3.0,        # 单期增持 ≥ 该百分点 → 更像战略意图
    # —— 股本变动（被动稀释/缩股）识别：本 skill 最大的假阳性源，见 quality_evidence ——
    "cap_min_holders": 3,         # 至少几个股东才敢推断股本变动
    "cap_consensus": 0.6,         # 多少比例的股东比值需聚在中位数附近
    "cap_tol": 0.01,              # 「聚在附近」的相对容差（±1%）
    "cap_min_dev": 0.005,         # 中位比值偏离 1 超过该值才认定发生股本变动
}


def _to_ts(s: Any) -> Optional[pd.Timestamp]:
    try:
        t = pd.to_datetime(str(s), format="%Y%m%d", errors="coerce")
        return None if pd.isna(t) else t
    except Exception:  # noqa: BLE001
        return None


def crossed_lines(prev: float, cur: float, lines: list) -> list:
    """返回本次由 prev → cur **向上穿越**的全部梯度线。

    通俗：上期持股 4.6%、这期 11%，那就是一口气越过了 5% 和 10% 两道线。
    """
    if prev is None or pd.isna(prev) or pd.isna(cur):
        return []
    return [float(L) for L in lines if float(prev) < float(L) <= float(cur)]


def dropped_lines(prev: float, cur: float, lines: list) -> list:
    """返回本次向下跌破的梯度线（举牌方减持退出，同样需要披露）。"""
    if prev is None or pd.isna(prev) or pd.isna(cur):
        return []
    return [float(L) for L in lines if float(cur) < float(L) <= float(prev)]


def infer_share_change(panel: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """推断每个 (票, 报告期) 的**股本变动系数 k**，用于剔除被动稀释/缩股造成的假越线。

    原理：总股本变化时，**所有未交易的股东持股比例会被同一个系数整体缩放**。
    若同一票同一报告期内多数股东的 `pct/prev_pct` 比值挤在同一个数上，那个数就是 k。

    通俗：公司增发新股，蛋糕变大了，谁也没卖，但每个人分到的那一块占比都变小了——
    这不叫减持。真机实测 301629.SZ 有 7 个股东比值全是 0.75000（发行 1/3 新股），
    600358.SH 六个股东全是 0.4341（发行股份购买资产，总股本涨到 2.3 倍）。
    不剔除的话这些会被全部误报成"减持跌破举牌线"。

    返回列：symbol / as_of_date / k / n_holders / consensus
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cols = ["symbol", "as_of_date", "k", "n_holders", "consensus"]
    if panel is None or panel.empty:
        e = pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
        for c in ("k", "n_holders", "consensus"):
            e[c] = pd.Series(dtype="float64")
        return e
    d = panel.copy()
    d["pct"] = pd.to_numeric(d["pct"], errors="coerce")
    d = d.dropna(subset=["pct"]).sort_values(["symbol", "holder_name", "as_of_date", "announce_date"])
    d["prev_pct"] = d.groupby(["symbol", "holder_name"], sort=False)["pct"].shift(1)
    d = d[(d["prev_pct"].notna()) & (d["prev_pct"] > 0)].copy()
    if d.empty:
        return infer_share_change(None, cfg)
    d["ratio"] = d["pct"] / d["prev_pct"]

    rows = []
    for (sym, aod), grp in d.groupby(["symbol", "as_of_date"]):
        r = grp["ratio"].dropna()
        if len(r) < cfg["cap_min_holders"]:
            continue
        med = float(r.median())
        if med <= 0 or abs(med - 1.0) <= cfg["cap_min_dev"]:
            continue                                  # 没有整体缩放 → 无股本变动
        consensus = float((abs(r / med - 1.0) <= cfg["cap_tol"]).mean())
        if consensus < cfg["cap_consensus"]:
            continue                                  # 比值不齐 → 不是统一缩放，别乱调
        rows.append({"symbol": sym, "as_of_date": aod, "k": round(med, 6),
                     "n_holders": int(len(r)), "consensus": round(consensus, 3)})
    return pd.DataFrame(rows, columns=cols) if rows else infer_share_change(None, cfg)


def detect_events(panel: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """从十大股东快照面板检出举牌 / 增持越线 / 减持跌破 事件。

    输入 panel 需含：symbol / announce_date / as_of_date / holder_name / pct /
                    holder_class / nature（datasource.normalize_holders 的输出）
    输出每行一个事件，按 **announce_date（公告日）** 排序——PIT 口径，回测无前视。
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cols = ["symbol", "announce_date", "as_of_date", "holder_name", "holder_class", "nature",
            "prev_pct", "base_pct", "pct", "delta_pct", "active_delta", "share_change_k",
            "change_kind", "event_type", "crossed_line", "top_line",
            "placard_round", "is_control_bid", "lock_until", "intent", "rank"]
    if panel is None or panel.empty:
        empty = pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
        for c in ("prev_pct", "base_pct", "pct", "delta_pct", "active_delta",
                  "share_change_k", "crossed_line", "top_line", "rank"):
            empty[c] = pd.Series(dtype="float64")
        return empty

    d = panel.copy()
    d["pct"] = pd.to_numeric(d["pct"], errors="coerce")
    d = d.dropna(subset=["pct"])
    # 按报告期排序求相邻期变动（同一票同一股东的时间序列）
    d = d.sort_values(["symbol", "holder_name", "as_of_date", "announce_date"])
    g = d.groupby(["symbol", "holder_name"], sort=False)
    d["prev_pct"] = g["pct"].shift(1)
    d["delta_pct"] = d["pct"] - d["prev_pct"]

    # 【关键】股本变动校正：base_pct = "若该股东完全没交易，本期应有的比例"
    caps = infer_share_change(panel, cfg)
    kmap = {(r.symbol, r.as_of_date): float(r.k) for r in caps.itertuples()} if not caps.empty else {}
    d["share_change_k"] = [kmap.get((s, a), 1.0) for s, a in zip(d["symbol"], d["as_of_date"])]
    d["base_pct"] = d["prev_pct"] * d["share_change_k"]
    d["active_delta"] = d["pct"] - d["base_pct"]

    rows = []
    for _, r in d.iterrows():
        prev, cur, base = r["prev_pct"], r["pct"], r["base_pct"]
        if pd.isna(prev) or pd.isna(base):
            continue                                   # 首次出现在十大股东表，无上期可比
        k = float(r["share_change_k"])
        active = cur - base
        if abs(cur - prev) < cfg["min_pct_move"]:
            continue                                   # 小数漂移
        # 越线判定用**实际比例** prev→cur（举牌线是按实际持股画的，基准不能改）；
        # 再减去"完全不交易、光靠股本变动也会发生"的那部分穿越 prev→base
        # ——剩下的才是**交易造成的**穿越，才算举牌/减持。
        up = [L for L in crossed_lines(prev, cur, cfg["lines"])
              if L not in crossed_lines(prev, base, cfg["lines"])]
        down = [L for L in dropped_lines(prev, cur, cfg["lines"])
                if L not in dropped_lines(prev, base, cfg["lines"])]
        if not up and not down:
            continue
        # 纯被动：本人没交易（active≈0）→ 不可能是举牌/减持
        if abs(active) < cfg["min_pct_move"]:
            continue
        change_kind = "active" if abs(k - 1.0) <= cfg["cap_min_dev"] else "mixed"
        if up:
            top = max(up)
            etype = "placard" if top == cfg["placard_line"] else "placard_raise"
            crossed, lock = up, _lock_until(r["announce_date"], cfg["lock_months"])
        else:
            top = min(down)
            etype = "placard_exit"
            crossed, lock = down, None
        rows.append({
            "symbol": r["symbol"],
            "announce_date": r["announce_date"],
            "as_of_date": r["as_of_date"],
            "holder_name": r["holder_name"],
            "holder_class": r.get("holder_class", "举牌人"),
            "nature": r.get("nature", ""),
            "prev_pct": round(float(prev), 4),
            "base_pct": round(float(base), 4),
            "pct": round(float(cur), 4),
            "delta_pct": round(float(cur - prev), 4),
            "active_delta": round(float(active), 4),
            "share_change_k": round(k, 6),
            "change_kind": change_kind,
            "event_type": etype,
            "crossed_line": top,
            "top_line": max(crossed),
            # 举牌轮次：跨过第几档（5%=1、10%=2 …），越高越像来真的
            "placard_round": int(top // cfg["placard_line"]) if top else 0,
            "is_control_bid": bool(cur >= cfg["control_line"]),
            "lock_until": lock,
            "intent": judge_intent(r, cur, active, cfg),
            "rank": r.get("rank"),
        })
    out = pd.DataFrame(rows, columns=cols) if rows else detect_events(None, cfg)
    if not out.empty:
        out = out.sort_values(["announce_date", "symbol", "pct"],
                              ascending=[False, True, False]).reset_index(drop=True)
    return out


def _lock_until(announce_date: Any, months: int) -> Optional[str]:
    """短线交易归入权锁定期截止日（举牌公告日 + 6 个月）。

    通俗：举牌方在这个日期之前反手卖，赚的钱要还给公司——所以短期内多半跑不掉。
    """
    t = _to_ts(announce_date)
    if t is None:
        return None
    return (t + pd.DateOffset(months=months)).strftime("%Y%m%d")


def judge_intent(row: Any, cur: float, delta: float, cfg: dict) -> str:
    """财务投资 vs 战略投资的**倾向性**判断（非定论，仅给先验）。

    依据三点：主体性质（产业资本更可能战略）、持股高度（越过 20%/30% 几乎必然战略）、
    单期增持强度（一次加 3 个点以上不像财务投资）。
    数据无"举牌目的"字段，故只给倾向 + 理由，绝不宣称确定。
    """
    nature = str(row.get("nature", "") if hasattr(row, "get") else "")
    if cur >= cfg["control_line"]:
        return "战略-控制权"
    reasons = 0
    if nature == "产业资本":
        reasons += 1
    if cur >= 20.0:
        reasons += 1
    if delta >= cfg["strategic_jump"]:
        reasons += 1
    if reasons >= 2:
        return "战略倾向"
    if nature == "金融资本" and delta < cfg["strategic_jump"]:
        return "财务倾向"
    return "待观察"


def approaching_watchlist(panel: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """逼近举牌线的观察名单：**还没到线但已贴近**的股东（下一期可能举牌）。

    通俗：这些人手上已经 4.x%，再买一点就得举手了——提前盯住。
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cols = ["symbol", "announce_date", "as_of_date", "holder_name", "holder_class",
            "nature", "pct", "next_line", "gap_to_line", "delta_pct"]
    if panel is None or panel.empty:
        empty = pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
        for c in ("pct", "next_line", "gap_to_line", "delta_pct"):
            empty[c] = pd.Series(dtype="float64")
        return empty
    d = panel.copy()
    d["pct"] = pd.to_numeric(d["pct"], errors="coerce")
    d = d.dropna(subset=["pct"])
    d = d.sort_values(["symbol", "holder_name", "as_of_date", "announce_date"])
    d["delta_pct"] = d.groupby(["symbol", "holder_name"], sort=False)["pct"].diff()
    # 只看每个 (票, 股东) 的最新一期
    latest = d.groupby(["symbol", "holder_name"], as_index=False).last()

    rows = []
    for _, r in latest.iterrows():
        nxt = next((float(L) for L in cfg["lines"] if float(L) > r["pct"]), None)
        if nxt is None:
            continue
        gap = nxt - r["pct"]
        if gap > cfg["approach_band"]:
            continue
        rows.append({
            "symbol": r["symbol"], "announce_date": r["announce_date"],
            "as_of_date": r["as_of_date"], "holder_name": r["holder_name"],
            "holder_class": r.get("holder_class", "举牌人"), "nature": r.get("nature", ""),
            "pct": round(float(r["pct"]), 4), "next_line": nxt,
            "gap_to_line": round(float(gap), 4),
            "delta_pct": None if pd.isna(r.get("delta_pct")) else round(float(r["delta_pct"]), 4),
        })
    out = pd.DataFrame(rows, columns=cols) if rows else approaching_watchlist(None, cfg)
    if not out.empty:
        out = out.sort_values(["gap_to_line", "symbol"]).reset_index(drop=True)
    return out


def plain_text(ev: dict) -> str:
    """把一条举牌事件写成一段人话研判（agent 可直接引用给用户）。"""
    sym, who = ev.get("symbol", ""), ev.get("holder_name", "")
    prev, cur, dl = ev.get("prev_pct"), ev.get("pct"), ev.get("delta_pct")
    et, line, rnd = ev.get("event_type"), ev.get("crossed_line"), ev.get("placard_round")
    nature, intent = ev.get("nature", ""), ev.get("intent", "")
    lock = ev.get("lock_until")

    if et == "placard":
        head = f"{who} 首次举牌 {sym}：持股由 {prev}% 增至 {cur}%（+{dl} 个百分点），越过 5% 举牌线。"
    elif et == "placard_raise":
        head = (f"{who} 继续加码 {sym} 至 {cur}%（+{dl} 个百分点），"
                f"越过 {line:.0f}% 线（第 {rnd} 档）——举得越高越像来真的。")
    else:
        head = f"{who} 减持 {sym} 至 {cur}%（{dl} 个百分点），跌破 {line:.0f}% 线，此前的举牌头寸在退出。"

    bits = [head]
    if nature:
        bits.append(f"主体性质：{nature}。")
    if intent == "战略-控制权":
        bits.append("持股已越过 30% 控制权线，触及要约收购/豁免区间，是冲着控制权来的。")
    elif intent == "战略倾向":
        bits.append("从主体性质与增持力度看更像战略投资（想进董事会），而非只赚差价。")
    elif intent == "财务倾向":
        bits.append("更像财务投资（赚差价为主），但数据无'举牌目的'字段，仅为倾向判断。")
    else:
        bits.append("意图待观察——数据无'举牌目的'字段，不宜过早定性。")
    if lock and et in ("placard", "placard_raise"):
        bits.append(f"短线交易归入权锁定至 {lock}（举牌后 6 个月内反手卖，收益须归公司），期间大概率跑不掉。")
    bits.append("（本口径为季度报告期快照重建，非实时举牌播报；不构成投资建议）")
    return "".join(bits)


def summarize(events: pd.DataFrame, watch: pd.DataFrame) -> dict:
    """市场级汇总（供 MARKET 汇总行与看板抬头）。"""
    def _n(df, **kw):
        if df is None or df.empty:
            return 0
        m = pd.Series(True, index=df.index)
        for k, v in kw.items():
            m &= (df[k] == v)
        return int(m.sum())
    return {
        "n_events": 0 if events is None or events.empty else int(len(events)),
        "n_placard": _n(events, event_type="placard"),
        "n_raise": _n(events, event_type="placard_raise"),
        "n_exit": _n(events, event_type="placard_exit"),
        "n_control_bid": 0 if events is None or events.empty else int(events["is_control_bid"].sum()),
        "n_strategic": 0 if events is None or events.empty
                       else int(events["intent"].astype(str).str.startswith("战略").sum()),
        "n_symbols": 0 if events is None or events.empty else int(events["symbol"].nunique()),
        "n_approaching": 0 if watch is None or watch.empty else int(len(watch)),
    }
