"""BUILD 入口（#17 举牌行为监控）：run / validate_input / backfill / maintain_daily / save_parquet。

输出 BUILD §11 标准面板，主键 `(trade_date, build_id="17", target_id, result_type)`。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import datasource as DS          # noqa: E402
import placard as P              # noqa: E402

BUILD_ID = "17"
BUILD_NAME = "举牌行为监控"
DATA_VERSION = "equity-placard-v1"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "生产产物" / "database.parquet"

_RESULT_COLS = ["trade_date", "build_id", "build_name", "target_id", "result_type",
                "result_value", "holder_name", "holder_class", "nature", "event_type",
                "prev_pct", "base_pct", "pct", "delta_pct", "active_delta",
                "share_change_k", "change_kind", "crossed_line", "placard_round",
                "is_control_bid", "lock_until", "intent", "as_of_date",
                "plain_text", "result_json", "source_data_date", "data_version", "update_time"]


# ============================================================================
# 输入校验
# ============================================================================
def validate_input(input_data: Any) -> dict:
    """校验并归一化入参。非法输入**抛错**，绝不静默兜底。"""
    if input_data is None:
        raise ValueError("input_data 不能为 None")
    if isinstance(input_data, str):
        input_data = {"symbols": [input_data]}
    if isinstance(input_data, (list, tuple)):
        input_data = {"symbols": list(input_data)}
    if not isinstance(input_data, dict):
        raise TypeError(f"input_data 需为 dict/list/str，实际 {type(input_data).__name__}")
    if not input_data:
        raise ValueError("input_data 为空 dict")

    mode = str(input_data.get("mode", "watchlist")).lower()
    if mode not in ("watchlist", "scan"):
        raise ValueError(f"mode 只支持 watchlist / scan，实际 {mode!r}")

    symbols = input_data.get("symbols")
    if isinstance(symbols, str):
        symbols = [symbols]
    if symbols is not None:
        symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if mode == "watchlist" and not symbols and input_data.get("panel") is None:
        raise ValueError("watchlist 模式必须提供 symbols（或直接传 panel 离线直连）")

    start, end = input_data.get("start"), input_data.get("end")
    for k, v in (("start", start), ("end", end)):
        if v is not None and not (isinstance(v, str) and len(str(v)) == 8 and str(v).isdigit()):
            raise ValueError(f"{k} 需为 YYYYMMDD 字符串，实际 {v!r}")
    if start and end and str(start) > str(end):
        raise ValueError(f"start({start}) 晚于 end({end})")

    return {"mode": mode, "symbols": symbols, "start": start, "end": end,
            "panel": input_data.get("panel")}


# ============================================================================
# 主入口
# ============================================================================
def run(input_data: Any, config: Optional[dict] = None) -> pd.DataFrame:
    """检出举牌事件并返回 BUILD §11 标准面板。

    三种输入：
      - watchlist：`{"symbols":["002011.SZ"], "start":"20240101", "end":"20260721"}`
      - scan     ：`{"mode":"scan", "start":"20250101", "end":"20260721"}`（symbol='' 全市场）
      - 直连     ：`{"panel": <已归一化的 DataFrame>}`（离线可用，跳过拉数）
    """
    args = validate_input(input_data)
    cfg = config or {}

    if args["panel"] is not None:
        panel = args["panel"]
        if not isinstance(panel, pd.DataFrame):
            raise TypeError("panel 需为 pandas.DataFrame")
    else:
        api = DS.init_panda()
        start, end = args["start"] or "20240101", args["end"] or datetime.now().strftime("%Y%m%d")
        if args["mode"] == "scan":
            panel = DS.load_top_holders(api, symbol="", start=start, end=end)
        else:
            panel = DS.chunk_pull(api, args["symbols"], start, end)

    events = P.detect_events(panel, cfg)
    watch = P.approaching_watchlist(panel, cfg)

    # 【按用户口径】默认剔除通道账户（北向托管/回购专户/国家队），但**单独成表**不丢弃：
    # 北向持股穿越 5% 本身是有信息量的资金信号，只是不叫举牌。
    real_ev = events[events["holder_class"] == "举牌人"] if not events.empty else events
    chan_ev = events[events["holder_class"] != "举牌人"] if not events.empty else events
    real_watch = watch[watch["holder_class"] == "举牌人"] if not watch.empty else watch

    return to_standard_output(real_ev, chan_ev, real_watch, panel)


def to_standard_output(events: pd.DataFrame, channel_events: pd.DataFrame,
                       watch: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """组装 BUILD §11 面板：placard_event / channel_flow / approaching / MARKET 汇总。"""
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    src = ""
    if panel is not None and not panel.empty and "announce_date" in panel:
        src = str(panel["announce_date"].max())

    rows: list[dict] = []
    for _, e in (events if events is not None else pd.DataFrame()).iterrows():
        d = e.to_dict()
        rows.append({**_base(d.get("announce_date"), d.get("symbol"), "placard_event", now, src),
                     "result_value": f"{d.get('event_type')}@{d.get('crossed_line'):.0f}%",
                     **_evt_fields(d), "plain_text": P.plain_text(d),
                     "result_json": json.dumps(d, ensure_ascii=False, default=str)})
    for _, e in (channel_events if channel_events is not None else pd.DataFrame()).iterrows():
        d = e.to_dict()
        rows.append({**_base(d.get("announce_date"), d.get("symbol"), "channel_flow", now, src),
                     "result_value": f"{d.get('holder_class')}@{d.get('crossed_line'):.0f}%",
                     **_evt_fields(d),
                     "plain_text": (f"{d.get('holder_name')} 持 {d.get('symbol')} 由 {d.get('prev_pct')}% "
                                    f"变为 {d.get('pct')}%，越过 {d.get('crossed_line'):.0f}% 线。"
                                    "该主体为通道/代持类账户（非举牌人），**不构成举牌**，"
                                    "仅作资金流向参考。"),
                     "result_json": json.dumps(d, ensure_ascii=False, default=str)})
    for _, w in (watch if watch is not None else pd.DataFrame()).iterrows():
        d = w.to_dict()
        rows.append({**_base(d.get("announce_date"), d.get("symbol"), "approaching", now, src),
                     "result_value": f"距{d.get('next_line'):.0f}%线{d.get('gap_to_line')}pt",
                     "holder_name": d.get("holder_name"), "holder_class": d.get("holder_class"),
                     "nature": d.get("nature"), "pct": d.get("pct"),
                     "delta_pct": d.get("delta_pct"), "as_of_date": d.get("as_of_date"),
                     "plain_text": (f"{d.get('holder_name')} 持 {d.get('symbol')} 已达 {d.get('pct')}%，"
                                    f"距 {d.get('next_line'):.0f}% 线仅 {d.get('gap_to_line')} 个百分点，"
                                    "再增持少量即触发披露义务，建议提前跟踪。"),
                     "result_json": json.dumps(d, ensure_ascii=False, default=str)})

    summ = P.summarize(events, watch)
    summ["n_channel_events"] = 0 if channel_events is None or channel_events.empty else int(len(channel_events))
    rows.append({**_base(src, "MARKET", "placard_summary", now, src),
                 "result_value": (f"{summ['n_placard']}首次举牌/{summ['n_raise']}加码/"
                                  f"{summ['n_exit']}退出/{summ['n_approaching']}逼近"),
                 "plain_text": (f"本区间检出首次举牌 {summ['n_placard']} 起、继续加码 {summ['n_raise']} 起、"
                                f"减持跌破 {summ['n_exit']} 起，涉及 {summ['n_symbols']} 只个股；"
                                f"其中战略倾向 {summ['n_strategic']} 起、越过 30% 控制权线 "
                                f"{summ['n_control_bid']} 起；另有 {summ['n_approaching']} 个逼近举牌线的观察对象、"
                                f"{summ['n_channel_events']} 起通道账户越线（不算举牌）。"
                                "口径：报告期快照重建，非实时播报。"),
                 "result_json": json.dumps(summ, ensure_ascii=False, default=str)})

    out = pd.DataFrame(rows, columns=_RESULT_COLS)
    return out


def _base(trade_date: Any, target: Any, rtype: str, now: str, src: str) -> dict:
    return {"trade_date": str(trade_date or ""), "build_id": BUILD_ID, "build_name": BUILD_NAME,
            "target_id": str(target or ""), "result_type": rtype,
            "source_data_date": src, "data_version": DATA_VERSION, "update_time": now}


def _evt_fields(d: dict) -> dict:
    return {k: d.get(k) for k in ("holder_name", "holder_class", "nature", "event_type",
                                  "prev_pct", "base_pct", "pct", "delta_pct", "active_delta",
                                  "share_change_k", "change_kind", "crossed_line",
                                  "placard_round", "is_control_bid", "lock_until",
                                  "intent", "as_of_date")}


# ============================================================================
# 生产维护
# ============================================================================
def maintain_daily(end: Optional[str] = None, lookback_days: int = 400,
                   config: Optional[dict] = None) -> pd.DataFrame:
    """每日增量：扫最近 lookback_days 的十大股东披露，检出新事件。"""
    end = end or datetime.now().strftime("%Y%m%d")
    start = (pd.Timestamp(end) - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d")
    return run({"mode": "scan", "start": start, "end": end}, config)


def backfill(start: str, end: str, config: Optional[dict] = None) -> pd.DataFrame:
    """历史回补（全市场区间扫描）。"""
    return run({"mode": "scan", "start": start, "end": end}, config)


def save_parquet(panel: pd.DataFrame, out: Path = DEFAULT_OUT) -> Path:
    """落盘（按主键去重，keep=last）。无 pyarrow 时降级 CSV。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = panel.copy()
    # as_of_date 必须进主键：一个公告日可能同时披露多个报告期的持股，
    # 漏掉它会让不同报告期的事件互相覆盖（实测丢失 12 条）。
    key = ["trade_date", "build_id", "target_id", "result_type", "holder_name", "as_of_date"]
    if out.exists():
        try:
            old = pd.read_parquet(out)
            df = pd.concat([old, df], ignore_index=True)
        except Exception:  # noqa: BLE001
            pass
    df = df.drop_duplicates(subset=[k for k in key if k in df.columns], keep="last")
    try:
        df.to_parquet(out, index=False)
    except Exception:  # noqa: BLE001 无 pyarrow → CSV 降级
        out = out.with_suffix(".csv")
        df.to_csv(out, index=False)
    return out


# ============================================================================
# CLI
# ============================================================================
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="举牌行为监控（#17）")
    ap.add_argument("--mode", default="watchlist", choices=["watchlist", "scan"])
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--save", action="store_true", help="落生产 parquet")
    ap.add_argument("--html", default=None, help="导出 HTML 看板到指定路径")
    a = ap.parse_args(argv)

    panel = run({"mode": a.mode, "symbols": a.symbols, "start": a.start, "end": a.end})
    ev = panel[panel.result_type == "placard_event"]
    print(f"举牌事件 {len(ev)} 起 | 通道越线 {int((panel.result_type=='channel_flow').sum())} 起 "
          f"| 逼近观察 {int((panel.result_type=='approaching').sum())} 个")
    for _, r in ev.head(15).iterrows():
        print(f"  [{r.trade_date}] {r.target_id} {r.holder_name} "
              f"{r.prev_pct}%→{r.pct}% ({r.event_type}, {r.intent})")
    summ = panel[panel.result_type == "placard_summary"]
    if not summ.empty:
        print("\n" + summ.iloc[0].plain_text)
    if a.save:
        print("已落盘:", save_parquet(panel))
    if a.html:
        import render
        Path(a.html).write_text(render.render_html(panel), encoding="utf-8")
        print("看板:", a.html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
