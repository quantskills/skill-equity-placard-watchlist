"""全离线自测（#17 举牌行为监控）。

设计原则：核心逻辑纯函数、零 IO，合成夹具即可覆盖全部判定分支；
真实数据用例可选，缺 SDK / 缺凭证 / 缺配额时**优雅跳过**而非判失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build as B          # noqa: E402
import datasource as DS    # noqa: E402
import placard as P        # noqa: E402
import render as R         # noqa: E402


# ---------- 夹具 ----------
def _raw(rows: list[dict]) -> pd.DataFrame:
    """构造 get_top_holders 原始返回形状（含 stock_type 双口径）。"""
    return pd.DataFrame(rows)


def _panel(rows: list[tuple]) -> pd.DataFrame:
    """(symbol, announce_date, as_of_date, holder_name, pct) → 归一化面板。"""
    return DS.normalize_holders(_raw([
        {"symbol": s, "date": ad, "end_date": aod, "holder_name": h, "stock_type": "total",
         "hold_percent_total": p, "hold_percent_float": p * 1.1, "rank": 1.0,
         "holder_kind": k, "holder_attr": ""}
        for s, ad, aod, h, p, k in rows]))


# ---------- 测试 ----------
def test_total_vs_flow_caliber():
    """【核心数据坑】必须只取 stock_type=total：flow 行的 hold_percent_total
    是在流通股本上算的，与举牌的总股本口径不同（真机实测格力持盾安：flow 25.38% vs total 38.46%）。"""
    raw = _raw([
        {"symbol": "002011.SZ", "date": "20250418", "end_date": "20241231",
         "holder_name": "珠海格力电器股份有限公司", "stock_type": "flow",
         "hold_percent_total": 25.38, "hold_percent_float": 29.51, "rank": 1.0,
         "holder_kind": "一般企业", "holder_attr": "企业"},
        {"symbol": "002011.SZ", "date": "20250418", "end_date": "20241231",
         "holder_name": "珠海格力电器股份有限公司", "stock_type": "total",
         "hold_percent_total": 38.46, "hold_percent_float": 29.51, "rank": 1.0,
         "holder_kind": "一般企业", "holder_attr": "企业"},
    ])
    n = DS.normalize_holders(raw)
    assert len(n) == 1, f"flow 行必须被剔除，实际保留 {len(n)} 行"
    assert abs(n.iloc[0]["pct"] - 38.46) < 1e-6, f"应取 total 口径 38.46，实际 {n.iloc[0]['pct']}"
    print(f"✅ test_total_vs_flow_caliber（只取 total 口径 ={n.iloc[0]['pct']}%，剔除 flow 的 25.38%）")


def test_first_placard_detected():
    """首次举牌：4.6% → 6.2% 上穿 5% 线。"""
    panel = _panel([
        ("601005.SH", "20260301", "20251231", "华宝投资有限公司", 4.60, "投资、咨询公司"),
        ("601005.SH", "20260624", "20260331", "华宝投资有限公司", 6.20, "投资、咨询公司"),
    ])
    ev = P.detect_events(panel)
    assert len(ev) == 1, f"应检出 1 起举牌，实际 {len(ev)}"
    e = ev.iloc[0]
    assert e.event_type == "placard" and e.crossed_line == 5.0, (e.event_type, e.crossed_line)
    assert e.placard_round == 1 and not e.is_control_bid
    assert e.trade_date if "trade_date" in ev.columns else True
    print(f"✅ test_first_placard_detected（{e.prev_pct}%→{e.pct}% 越 5% 线，轮次 {e.placard_round}）")


def test_multi_line_jump():
    """一口气跨多档：1.55% → 16.0% 应识别越过 5/10/15 三条线，取最高档。"""
    panel = _panel([
        ("601005.SH", "20260301", "20251231", "某产业集团有限公司", 1.55, "一般企业"),
        ("601005.SH", "20260624", "20260331", "某产业集团有限公司", 16.0, "一般企业"),
    ])
    ev = P.detect_events(panel)
    e = ev.iloc[0]
    assert P.crossed_lines(1.55, 16.0, P.DEFAULT_CONFIG["lines"]) == [5.0, 10.0, 15.0]
    assert e.crossed_line == 15.0 and e.placard_round == 3, (e.crossed_line, e.placard_round)
    assert e.event_type == "placard_raise"
    print(f"✅ test_multi_line_jump（跨 5/10/15 三档，最高档 {e.crossed_line:.0f}%，轮次 {e.placard_round}）")


def test_channel_account_excluded():
    """【最大假阳性源】北向托管/回购专户越线不得算举牌，但要单独成表不丢弃。"""
    panel = _panel([
        ("002532.SZ", "20260301", "20251231", "香港中央结算有限公司", 3.13, "一般企业"),
        ("002532.SZ", "20260707", "20260331", "香港中央结算有限公司", 5.23, "一般企业"),
        ("688326.SH", "20260301", "20251231", "某公司回购专用证券账户", 4.34, "其他金融产品"),
        ("688326.SH", "20260717", "20260331", "某公司回购专用证券账户", 6.18, "其他金融产品"),
        ("601005.SH", "20260301", "20251231", "华宝投资有限公司", 4.60, "投资、咨询公司"),
        ("601005.SH", "20260624", "20260331", "华宝投资有限公司", 6.20, "投资、咨询公司"),
    ])
    assert DS.is_channel("香港中央结算有限公司") and DS.is_channel("某公司回购专用证券账户")
    assert not DS.is_channel("华宝投资有限公司")
    out = B.run({"panel": panel})
    real = out[out.result_type == "placard_event"]
    chan = out[out.result_type == "channel_flow"]
    assert len(real) == 1 and real.iloc[0].holder_name == "华宝投资有限公司", list(real.holder_name)
    assert len(chan) == 2, f"通道账户应单独成表 2 条，实际 {len(chan)}"
    assert "不构成举牌" in chan.iloc[0].plain_text
    print(f"✅ test_channel_account_excluded（真举牌 {len(real)} 起 / 通道单独成表 {len(chan)} 起）")


def test_share_change_inferred():
    """股本变动系数推断：多股东比值齐刷刷缩到同一个数 → 认定股本扩张。"""
    rows = []
    for h, p in [("何沁修", 12.2198), ("杨波", 12.2198), ("王胜利", 12.2198),
                 ("胡泓", 12.2198), ("辜国文", 12.2198), ("西博壹号", 6.0610), ("丰年君和", 5.4849)]:
        rows.append(("301629.SZ", "20250301", "20241231", h, p, "一般企业"))
        rows.append(("301629.SZ", "20250324", "20250324", h, round(p * 0.75, 4), "一般企业"))
    caps = P.infer_share_change(_panel(rows))
    assert len(caps) == 1, f"应识别 1 个股本变动组合，实际 {len(caps)}"
    c = caps.iloc[0]
    assert abs(c.k - 0.75) < 1e-4 and c.n_holders == 7 and c.consensus == 1.0, c.to_dict()
    print(f"✅ test_share_change_inferred（k={c.k}，{c.n_holders} 个股东一致度 {c.consensus}）")


def test_passive_dilution_not_reported():
    """【最大假阳性源】发行新股导致的被动稀释不得报成减持跌破。

    真机实测：301629.SZ 七个股东比值全是 0.75000（发行 1/3 新股）、
    600358.SH 六个股东全是 0.4341（发行股份购买资产），修复前全被误报成"减持跌破举牌线"。
    """
    rows = []
    for h, p in [("甲", 12.2198), ("乙", 12.2198), ("丙", 12.2198),
                 ("丁", 6.0610), ("戊", 5.4849)]:
        rows.append(("301629.SZ", "20250301", "20241231", h, p, "一般企业"))
        rows.append(("301629.SZ", "20250324", "20250324", h, round(p * 0.75, 4), "一般企业"))
    panel = _panel(rows)
    # 未校正时这些会被误报（用极大 cap_min_dev 关掉校正来对照）
    naive = P.detect_events(panel, {"cap_min_dev": 9e9})
    assert len(naive) >= 3, f"对照组应有误报，实际 {len(naive)}"
    ev = P.detect_events(panel)
    assert ev.empty, f"纯被动稀释不得产生事件，实际 {len(ev)} 条：{list(ev.event_type)}"
    print(f"✅ test_passive_dilution_not_reported（对照组误报 {len(naive)} 条 → 校正后 0 条）")


def test_active_sale_during_dilution_kept():
    """稀释期间真减持仍要检出：别把校正做成一刀切的静音。"""
    rows = []
    for h, p in [("甲", 12.0), ("乙", 12.0), ("丙", 12.0), ("丁", 12.0)]:
        rows.append(("X.SZ", "20250301", "20241231", h, p, "一般企业"))
        rows.append(("X.SZ", "20250324", "20250324", h, 9.0, "一般企业"))     # 纯稀释 k=0.75
    # 真卖家：同样稀释，但还多卖了一大截（9.0 → 4.0，跌破 5%）
    rows.append(("X.SZ", "20250301", "20241231", "真卖家", 12.0, "一般企业"))
    rows.append(("X.SZ", "20250324", "20250324", "真卖家", 4.0, "一般企业"))
    ev = P.detect_events(_panel(rows))
    assert len(ev) == 1 and ev.iloc[0].holder_name == "真卖家", list(ev.holder_name)
    e = ev.iloc[0]
    assert e.event_type == "placard_exit" and e.crossed_line == 5.0
    assert abs(e.active_delta - (4.0 - 9.0)) < 0.01, e.active_delta
    assert e.change_kind == "mixed", e.change_kind
    print(f"✅ test_active_sale_during_dilution_kept（真卖家检出，主动减持 {e.active_delta}pt，其余 4 人静音）")


def test_buyback_cancel_passive_rise_not_placard():
    """反向同理：回购注销使总股本缩小，所有人比例被动升破 5% —— 那不是举牌。"""
    rows = []
    for h, p in [("甲", 4.8), ("乙", 4.8), ("丙", 4.8), ("丁", 4.8)]:
        rows.append(("Y.SZ", "20250301", "20241231", h, p, "一般企业"))
        rows.append(("Y.SZ", "20250630", "20250630", h, round(p * 1.10, 4), "一般企业"))  # k=1.10 缩股
    ev = P.detect_events(_panel(rows))
    assert ev.empty, f"被动升破不得算举牌，实际 {len(ev)} 条：{list(ev.event_type)}"
    naive = P.detect_events(_panel(rows), {"cap_min_dev": 9e9})
    assert len(naive) == 4, f"对照组应误报 4 起'举牌'，实际 {len(naive)}"
    print(f"✅ test_buyback_cancel_passive_rise_not_placard（对照组误报 {len(naive)} 起 → 校正后 0 起）")


def test_dilution_needs_consensus():
    """比值不一致时不得乱调：只有 1 人变动 ≠ 股本变动，必须照常报事件。"""
    rows = [("Z.SZ", "20250301", "20241231", h, p, "一般企业")
            for h, p in [("甲", 12.0), ("乙", 8.0), ("丙", 6.0)]]
    rows += [("Z.SZ", "20250630", "20250630", "甲", 12.0, "一般企业"),
             ("Z.SZ", "20250630", "20250630", "乙", 8.0, "一般企业"),
             ("Z.SZ", "20250630", "20250630", "丙", 4.0, "一般企业")]   # 只有丙在卖
    caps = P.infer_share_change(_panel(rows))
    assert caps.empty, f"比值不一致不应认定股本变动，实际 {caps.to_dict('records')}"
    ev = P.detect_events(_panel(rows))
    assert len(ev) == 1 and ev.iloc[0].holder_name == "丙" and ev.iloc[0].change_kind == "active"
    print("✅ test_dilution_needs_consensus（单人变动不误判为股本变动，照常报事件）")


def test_exit_and_no_false_event():
    """减持跌破要检出；未越线的正常波动不得产生事件。"""
    panel = _panel([
        ("002011.SZ", "20250418", "20241231", "紫金矿业投资(上海)有限公司", 8.36, "投资、咨询公司"),
        ("002011.SZ", "20260429", "20251231", "紫金矿业投资(上海)有限公司", 4.90, "投资、咨询公司"),
        ("000001.SZ", "20250418", "20241231", "某基金", 6.20, "开放式投资基金"),
        ("000001.SZ", "20260429", "20251231", "某基金", 7.10, "开放式投资基金"),   # 未越线
    ])
    ev = P.detect_events(panel)
    assert len(ev) == 1, f"仅 1 起跌破事件，实际 {len(ev)}：{list(ev.event_type)}"
    e = ev.iloc[0]
    assert e.event_type == "placard_exit" and e.crossed_line == 5.0
    assert e.lock_until is None, "退出事件不应有锁定期"
    print(f"✅ test_exit_and_no_false_event（跌破 5% 检出；6.2→7.1 未越线不误报）")


def test_lock_period_six_months():
    """举牌后 6 个月短线交易归入权锁定期。"""
    panel = _panel([
        ("601005.SH", "20260101", "20251231", "华宝投资有限公司", 4.60, "投资、咨询公司"),
        ("601005.SH", "20260624", "20260331", "华宝投资有限公司", 6.20, "投资、咨询公司"),
    ])
    e = P.detect_events(panel).iloc[0]
    assert e.lock_until == "20261224", f"20260624 + 6 个月应为 20261224，实际 {e.lock_until}"
    assert "20261224" in P.plain_text(e.to_dict())
    print(f"✅ test_lock_period_six_months（公告 20260624 → 锁定至 {e.lock_until}）")


def test_intent_and_control_line():
    """意图画像：产业资本大幅增持→战略；金融资本小幅→财务；越 30% →控制权。"""
    panel = _panel([
        ("A.SZ", "20260101", "20251231", "某某实业集团有限公司", 1.0, "一般企业"),
        ("A.SZ", "20260401", "20260331", "某某实业集团有限公司", 22.0, "一般企业"),
        ("B.SZ", "20260101", "20251231", "某某基金管理有限公司", 4.6, "开放式投资基金"),
        ("B.SZ", "20260401", "20260331", "某某基金管理有限公司", 5.2, "开放式投资基金"),
        ("C.SZ", "20260101", "20251231", "某某控股集团有限公司", 28.0, "一般企业"),
        ("C.SZ", "20260401", "20260331", "某某控股集团有限公司", 31.0, "一般企业"),
    ])
    ev = P.detect_events(panel).set_index("symbol")
    assert ev.loc["A.SZ", "intent"] == "战略倾向", ev.loc["A.SZ", "intent"]
    assert ev.loc["B.SZ", "intent"] == "财务倾向", ev.loc["B.SZ", "intent"]
    assert ev.loc["C.SZ", "intent"] == "战略-控制权" and bool(ev.loc["C.SZ", "is_control_bid"])
    print("✅ test_intent_and_control_line（产业→战略 / 金融小幅→财务 / 越30%→控制权）")


def test_approaching_watchlist():
    """逼近举牌线：4.6% 距 5% 仅 0.4pt 应入观察名单；3.0% 不入。"""
    panel = _panel([
        ("A.SZ", "20260401", "20260331", "逼近方", 4.60, "一般企业"),
        ("B.SZ", "20260401", "20260331", "远离方", 3.00, "一般企业"),
        ("C.SZ", "20260401", "20260331", "九点五", 9.50, "一般企业"),
    ])
    wa = P.approaching_watchlist(panel)
    got = dict(zip(wa.symbol, wa.next_line))
    assert "A.SZ" in got and got["A.SZ"] == 5.0, got
    assert "C.SZ" in got and got["C.SZ"] == 10.0, got
    assert "B.SZ" not in got, "距离超过预警带不应入名单"
    print(f"✅ test_approaching_watchlist（入选 {sorted(got)}，3.0% 正确排除）")


def test_pit_announce_date():
    """PIT：事件时间戳用**公告日**而非报告期，回测无前视。"""
    panel = _panel([
        ("A.SZ", "20260301", "20251231", "某方", 4.60, "一般企业"),
        ("A.SZ", "20260624", "20260331", "某方", 6.20, "一般企业"),
    ])
    e = P.detect_events(panel).iloc[0]
    assert e.announce_date == "20260624" and e.as_of_date == "20260331"
    out = B.run({"panel": panel})
    row = out[out.result_type == "placard_event"].iloc[0]
    assert row.trade_date == "20260624", f"面板 trade_date 应为公告日，实际 {row.trade_date}"
    assert row.as_of_date == "20260331", "报告期须同时保留供复盘"
    print("✅ test_pit_announce_date（trade_date=公告日 20260624，as_of_date=报告期 20260331）")


def test_dedup_mixed_report_periods():
    """一个公告日混多个报告期 + 同一(票,股东,报告期)重复 → 按最早公告日去重。"""
    raw = _raw([
        {"symbol": "X.SZ", "date": "20260429", "end_date": "20251231", "holder_name": "甲",
         "stock_type": "total", "hold_percent_total": 6.0, "hold_percent_float": 7.0,
         "rank": 1.0, "holder_kind": "一般企业", "holder_attr": ""},
        {"symbol": "X.SZ", "date": "20260520", "end_date": "20251231", "holder_name": "甲",
         "stock_type": "total", "hold_percent_total": 6.0, "hold_percent_float": 7.0,
         "rank": 1.0, "holder_kind": "一般企业", "holder_attr": ""},
        {"symbol": "X.SZ", "date": "20260429", "end_date": "20260331", "holder_name": "甲",
         "stock_type": "total", "hold_percent_total": 6.5, "hold_percent_float": 7.5,
         "rank": 1.0, "holder_kind": "一般企业", "holder_attr": ""},
    ])
    n = DS.normalize_holders(raw)
    assert len(n) == 2, f"同(票,股东,报告期)应去重为 2 行，实际 {len(n)}"
    first = n[n.as_of_date == "20251231"].iloc[0]
    assert first.announce_date == "20260429", f"应保留最早公告日，实际 {first.announce_date}"
    print("✅ test_dedup_mixed_report_periods（混合报告期去重，保留最早公告日）")


def test_noise_filtered():
    """股本微调造成的小数漂移（<0.01pt）不得产生事件。"""
    panel = _panel([
        ("A.SZ", "20260101", "20251231", "某方", 4.9995, "一般企业"),
        ("A.SZ", "20260401", "20260331", "某方", 5.0000, "一般企业"),
    ])
    ev = P.detect_events(panel)
    assert ev.empty, f"微幅漂移不应报举牌，实际 {len(ev)} 条"
    # 但真实跨线（0.1pt 以上）要报
    panel2 = _panel([
        ("A.SZ", "20260101", "20251231", "某方", 4.90, "一般企业"),
        ("A.SZ", "20260401", "20260331", "某方", 5.05, "一般企业"),
    ])
    assert len(P.detect_events(panel2)) == 1
    print("✅ test_noise_filtered（<0.01pt 漂移不误报；真实跨线仍检出）")


def test_validate_input():
    """非法入参必须抛错，不静默兜底。"""
    for bad, exc in ((None, ValueError), ({}, ValueError), (123, TypeError),
                     ({"mode": "bogus"}, ValueError), ({"mode": "watchlist"}, ValueError),
                     ({"symbols": ["A"], "start": "2026"}, ValueError),
                     ({"symbols": ["A"], "start": "20260401", "end": "20260101"}, ValueError)):
        try:
            B.validate_input(bad)
            raise AssertionError(f"{bad!r} 应抛错")
        except exc:
            pass
    assert B.validate_input("002011.SZ")["symbols"] == ["002011.SZ"]
    assert B.validate_input(["a.sz", "b.sh"])["symbols"] == ["A.SZ", "B.SH"]
    assert B.validate_input({"mode": "scan"})["mode"] == "scan"
    print("✅ test_validate_input（None/空/类型/模式/日期格式/日期倒序 全抛错）")


def test_empty_and_missing_cols():
    """空数据与缺列不得崩溃。"""
    assert DS.normalize_holders(None).empty
    assert DS.normalize_holders(pd.DataFrame()).empty
    assert P.detect_events(None).empty and P.approaching_watchlist(None).empty
    # 全 flow 行 → 过滤后为空
    raw = _raw([{"symbol": "A", "date": "20260401", "end_date": "20260331", "holder_name": "甲",
                 "stock_type": "flow", "hold_percent_total": 6.0, "hold_percent_float": 7.0,
                 "rank": 1.0, "holder_kind": "", "holder_attr": ""}])
    assert DS.normalize_holders(raw).empty
    out = B.run({"panel": DS.normalize_holders(None)})
    assert len(out) == 1 and out.iloc[0].result_type == "placard_summary", "空数据仍须出 MARKET 汇总行"
    print("✅ test_empty_and_missing_cols（空/缺列/全 flow 不崩；空日仍出 MARKET 行）")


def test_output_schema():
    """BUILD §11 面板合规：主键齐全、必填字段非空、result_json 可解析。"""
    import json
    panel = _panel([
        ("601005.SH", "20260101", "20251231", "华宝投资有限公司", 4.60, "投资、咨询公司"),
        ("601005.SH", "20260624", "20260331", "华宝投资有限公司", 6.20, "投资、咨询公司"),
    ])
    out = B.run({"panel": panel})
    for c in ("trade_date", "build_id", "build_name", "target_id", "result_type",
              "result_value", "data_version", "update_time"):
        assert c in out.columns, f"缺字段 {c}"
        assert out[c].notna().all(), f"{c} 存在空值"
    assert (out.build_id == "17").all() and (out.data_version == "equity-placard-v1").all()
    assert set(out.result_type) <= {"placard_event", "channel_flow", "approaching", "placard_summary"}
    for s in out.result_json:
        json.loads(s)
    # 主键唯一（as_of_date 必须在内：一个公告日可携带多个报告期）
    key = ["trade_date", "build_id", "target_id", "result_type", "holder_name", "as_of_date"]
    assert not out.duplicated(subset=key).any(), "主键重复"
    # 稀释校正的审计字段必须落盘，否则无法追溯为何某事件被保留/剔除
    for c in ("base_pct", "active_delta", "share_change_k", "change_kind"):
        assert c in out.columns, f"缺审计字段 {c}"
    print(f"✅ test_output_schema（BUILD §11 合规，{len(out)} 行，主键唯一）")


def test_render():
    """markdown 双行格式 + HTML 看板自包含。"""
    panel = _panel([
        ("601005.SH", "20260101", "20251231", "华宝投资有限公司", 4.60, "投资、咨询公司"),
        ("601005.SH", "20260624", "20260331", "华宝投资有限公司", 6.20, "投资、咨询公司"),
        ("002532.SZ", "20260101", "20251231", "香港中央结算有限公司", 3.13, "一般企业"),
        ("002532.SZ", "20260707", "20260331", "香港中央结算有限公司", 5.23, "一般企业"),
        ("A.SZ", "20260401", "20260331", "逼近方", 4.60, "一般企业"),
    ])
    out = B.run({"panel": panel})
    md = R.render_markdown(out)
    assert "学术读数" in md and "通俗解读" in md and "术语表" in md
    assert "华宝投资有限公司" in md and "不算举牌" in md
    h = R.render_html(out)
    assert h.startswith("<!doctype html>") and "</html>" in h
    assert "http://" not in h and "https://" not in h, "看板须自包含，不得外链"
    assert "<svg" in h and "举牌行为监控" in h
    print(f"✅ test_render（markdown {len(md)} 字 / HTML {len(h)} 字，自包含无外链）")


def test_plain_text_dynamic():
    """人话研判须按实际读数插值，不是静态模板。"""
    panel = _panel([
        ("A.SZ", "20260101", "20251231", "某某实业集团有限公司", 1.0, "一般企业"),
        ("A.SZ", "20260401", "20260331", "某某实业集团有限公司", 22.0, "一般企业"),
        ("B.SZ", "20260101", "20251231", "某某基金管理有限公司", 4.6, "开放式投资基金"),
        ("B.SZ", "20260401", "20260331", "某某基金管理有限公司", 5.2, "开放式投资基金"),
    ])
    ev = P.detect_events(panel)
    texts = {r.symbol: P.plain_text(r.to_dict()) for _, r in ev.iterrows()}
    assert "22.0%" in texts["A.SZ"] and "战略" in texts["A.SZ"]
    assert "5.2%" in texts["B.SZ"] and "首次举牌" in texts["B.SZ"]
    assert texts["A.SZ"] != texts["B.SZ"], "两条研判不得雷同"
    # 边界声明只应在汇总行出现一次，不再逐条重复（1781 条事件塞 1781 份副本是噪声）
    assert not any("不构成投资建议" in t for t in texts.values()), "逐条事件不应再重复免责"
    out = B.run({"panel": panel})
    summ = out[out.result_type == "placard_summary"].iloc[0].plain_text
    assert "非实时播报" in summ, "边界声明须保留在汇总行"
    print("✅ test_plain_text_dynamic（读数插值；边界只在汇总行出现一次）")


def test_real_data_optional():
    """真实数据（可选）：缺 SDK / 凭证 / 配额时优雅跳过。"""
    try:
        api = DS.init_panda()
        panel = DS.load_top_holders(api, symbol="002011.SZ", start="20230101", end="20260721")
        assert not panel.empty, "真实拉取为空"
        assert (panel["pct"] <= 100).all() and (panel["pct"] >= 0).all()
        ev = P.detect_events(panel)
        out = B.run({"panel": panel})
        assert not out.empty
        print(f"✅ test_real_data_optional（真实 002011.SZ：{len(panel)} 条快照 / {len(ev)} 起事件）")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if any(k in msg for k in ("配额", "quota", "凭证", "401", "403", "Connection", "timeout",
                                  "无法导入", "panda_data", "pip", "No module")):
            print(f"⏭️  test_real_data_optional 跳过（无 SDK/凭证/配额：{type(e).__name__}）")
        else:
            raise


if __name__ == "__main__":
    test_total_vs_flow_caliber()
    test_first_placard_detected()
    test_multi_line_jump()
    test_channel_account_excluded()
    test_share_change_inferred()
    test_passive_dilution_not_reported()
    test_active_sale_during_dilution_kept()
    test_buyback_cancel_passive_rise_not_placard()
    test_dilution_needs_consensus()
    test_exit_and_no_false_event()
    test_lock_period_six_months()
    test_intent_and_control_line()
    test_approaching_watchlist()
    test_pit_announce_date()
    test_dedup_mixed_report_periods()
    test_noise_filtered()
    test_validate_input()
    test_empty_and_missing_cols()
    test_output_schema()
    test_render()
    test_plain_text_dynamic()
    test_real_data_optional()
    print("\n🎉 全部测试通过")
