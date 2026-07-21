"""举牌监控渲染：markdown 档案 + 暗色 HTML 看板（学术读数 + 通俗解读双行）。

零外部依赖、零联网：HTML 自包含（内联 CSS + 内联 SVG），可直接双击打开。
"""
from __future__ import annotations

import html
from typing import Optional

import pandas as pd

import placard as P

EVENT_LABEL = {"placard": "首次举牌", "placard_raise": "继续加码", "placard_exit": "减持跌破"}
EVENT_COLOR = {"placard": "#22c55e", "placard_raise": "#3b82f6", "placard_exit": "#ef4444"}
INTENT_COLOR = {"战略-控制权": "#a855f7", "战略倾向": "#f59e0b", "财务倾向": "#38bdf8", "待观察": "#94a3b8"}


def _split(panel: pd.DataFrame) -> tuple:
    if panel is None or panel.empty:
        e = pd.DataFrame()
        return e, e, e, {}
    ev = panel[panel.result_type == "placard_event"]
    ch = panel[panel.result_type == "channel_flow"]
    wa = panel[panel.result_type == "approaching"]
    su = panel[panel.result_type == "placard_summary"]
    summary = {}
    if not su.empty:
        import json
        try:
            summary = json.loads(su.iloc[0].result_json)
        except Exception:  # noqa: BLE001
            summary = {}
        summary["plain_text"] = su.iloc[0].plain_text
    return ev, ch, wa, summary


def render_markdown(panel: pd.DataFrame) -> str:
    """markdown 档案：概览 + 举牌事件明细 + 逼近观察 + 术语表。"""
    ev, ch, wa, su = _split(panel)
    L: list[str] = ["# 举牌行为监控（#17）", ""]
    if su:
        L += ["## 概览", "", f"**学术读数**：事件 {su.get('n_events', 0)} 起"
              f"（首次举牌 {su.get('n_placard', 0)} / 加码 {su.get('n_raise', 0)} / "
              f"退出 {su.get('n_exit', 0)}），涉及 {su.get('n_symbols', 0)} 只；"
              f"战略倾向 {su.get('n_strategic', 0)}、控制权级 {su.get('n_control_bid', 0)}。", "",
              f"**通俗解读**：{su.get('plain_text', '')}", ""]

    L += ["## 举牌事件", ""]
    if ev.empty:
        L += ["_区间内无举牌事件。_", ""]
    else:
        L += ["| 公告日 | 代码 | 举牌方 | 性质 | 变动 | 越线 | 类型 | 意图 | 锁定至 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for _, r in ev.iterrows():
            L.append(f"| {r.trade_date} | {r.target_id} | {r.holder_name} | {r.nature} | "
                     f"{r.prev_pct}%→{r.pct}% | {_fmt_line(r.crossed_line)} | "
                     f"{EVENT_LABEL.get(r.event_type, r.event_type)} | {r.intent} | {r.lock_until or '—'} |")
        L.append("")
        L += ["### 逐条人话研判", ""]
        for _, r in ev.head(20).iterrows():
            L.append(f"- **{r.target_id} · {r.holder_name}**：{r.plain_text}")
        L.append("")

    if not wa.empty:
        L += ["## 逼近举牌线（观察名单）", "",
              "| 代码 | 股东 | 当前持股 | 下一条线 | 还差 |", "|---|---|---|---|---|"]
        for _, r in wa.iterrows():
            import json
            d = json.loads(r.result_json)
            L.append(f"| {r.target_id} | {r.holder_name} | {d.get('pct')}% | "
                     f"{_fmt_line(d.get('next_line'))} | {d.get('gap_to_line')} pt |")
        L.append("")

    if not ch.empty:
        L += ["## 通道账户越线（**不算举牌**，仅资金参考）", "",
              "| 公告日 | 代码 | 账户 | 类别 | 变动 |", "|---|---|---|---|---|"]
        for _, r in ch.head(30).iterrows():
            L.append(f"| {r.trade_date} | {r.target_id} | {r.holder_name} | {r.holder_class} | "
                     f"{r.prev_pct}%→{r.pct}% |")
        L.append("")

    L += ["## 术语表（学术 → 人话）", "", "| 术语 | 人话 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in P.GLOSSARY.items()]
    L += ["", "> 本口径为**报告期快照重建**，非实时举牌播报；仅研究与教育示例，不构成投资建议。"]
    return "\n".join(L)


def _fmt_line(v) -> str:
    try:
        return f"{float(v):.0f}%"
    except Exception:  # noqa: BLE001
        return str(v or "—")


def _bar_svg(su: dict) -> str:
    """事件构成条形图（内联 SVG，无外部依赖）。"""
    items = [("首次举牌", su.get("n_placard", 0), "#22c55e"),
             ("继续加码", su.get("n_raise", 0), "#3b82f6"),
             ("减持退出", su.get("n_exit", 0), "#ef4444"),
             ("逼近观察", su.get("n_approaching", 0), "#f59e0b")]
    mx = max([v for _, v, _ in items] + [1])
    w, bh, gap = 460, 26, 12
    h = len(items) * (bh + gap) + 10
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" aria-label="事件构成">']
    for i, (label, val, color) in enumerate(items):
        y = i * (bh + gap) + 5
        bw = max(2, int((val / mx) * (w - 190)))
        parts.append(f'<text x="0" y="{y+17}" fill="#cbd5e1" font-size="13">{html.escape(label)}</text>')
        parts.append(f'<rect x="80" y="{y}" width="{bw}" height="{bh}" rx="4" fill="{color}" opacity="0.85"/>')
        parts.append(f'<text x="{88+bw}" y="{y+17}" fill="#e2e8f0" font-size="13" font-weight="600">{val}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _cap_note(shown: int, total: int, what: str) -> str:
    """截断显式声明——绝不静默截断（截断了却不说 = 让人以为看到了全部）。"""
    if shown >= total:
        return ""
    return (f'<div class="sub" style="margin:8px 0 0">⚠️ 共 {total} 条{what}，'
            f'本表仅显示前 {shown} 条；完整数据见 <code>database.parquet</code>。</div>')


def render_html(panel: pd.DataFrame, title: str = "举牌行为监控 · #17",
                max_rows: int = 300) -> str:
    """暗色自包含 HTML 看板。`max_rows` 控制每表最大行数，超出**显式声明**不静默截断。"""
    ev, ch, wa, su = _split(panel)
    esc = html.escape
    n_ev, n_ch, n_wa = len(ev), len(ch), len(wa)
    # 举牌 / 加码优先展示（本 skill 的主角），减持跌破排后
    if not ev.empty:
        order = {"placard": 0, "placard_raise": 1, "placard_exit": 2}
        ev = ev.assign(_o=ev.event_type.map(order).fillna(9)).sort_values(
            ["_o", "trade_date"], ascending=[True, False]).drop(columns="_o").head(max_rows)
    ch = ch.head(max_rows) if not ch.empty else ch
    wa = wa.head(max_rows) if not wa.empty else wa

    def rows_ev(df):
        if df.empty:
            return '<tr><td colspan="9" class="empty">区间内无举牌事件</td></tr>'
        out = []
        for _, r in df.iterrows():
            c = EVENT_COLOR.get(r.event_type, "#94a3b8")
            ic = INTENT_COLOR.get(str(r.intent), "#94a3b8")
            ctrl = ' <span class="tag ctrl">控制权</span>' if r.is_control_bid else ""
            out.append(
                f'<tr><td class="mono">{esc(str(r.trade_date))}</td>'
                f'<td class="mono">{esc(str(r.target_id))}</td>'
                f'<td>{esc(str(r.holder_name))}{ctrl}</td>'
                f'<td>{esc(str(r.nature))}</td>'
                f'<td class="mono">{r.prev_pct}% → <b>{r.pct}%</b></td>'
                f'<td class="mono">{_fmt_line(r.crossed_line)}</td>'
                f'<td><span class="tag" style="background:{c}22;color:{c};border-color:{c}55">'
                f'{esc(EVENT_LABEL.get(r.event_type, str(r.event_type)))}</span></td>'
                f'<td><span class="tag" style="background:{ic}22;color:{ic};border-color:{ic}55">'
                f'{esc(str(r.intent))}</span></td>'
                f'<td class="mono dim">{esc(str(r.lock_until or "—"))}</td></tr>')
        return "".join(out)

    def rows_wa(df):
        if df.empty:
            return '<tr><td colspan="5" class="empty">无逼近举牌线的观察对象</td></tr>'
        import json
        out = []
        for _, r in df.iterrows():
            d = json.loads(r.result_json)
            out.append(f'<tr><td class="mono">{esc(str(r.target_id))}</td>'
                       f'<td>{esc(str(r.holder_name))}</td>'
                       f'<td class="mono">{d.get("pct")}%</td>'
                       f'<td class="mono">{_fmt_line(d.get("next_line"))}</td>'
                       f'<td class="mono warn">还差 {d.get("gap_to_line")} pt</td></tr>')
        return "".join(out)

    def rows_ch(df):
        if df.empty:
            return '<tr><td colspan="5" class="empty">无通道账户越线</td></tr>'
        out = []
        for _, r in df.iterrows():
            out.append(f'<tr><td class="mono">{esc(str(r.trade_date))}</td>'
                       f'<td class="mono">{esc(str(r.target_id))}</td>'
                       f'<td>{esc(str(r.holder_name))}</td>'
                       f'<td><span class="tag chan">{esc(str(r.holder_class))}</span></td>'
                       f'<td class="mono">{r.prev_pct}% → {r.pct}%</td></tr>')
        return "".join(out)

    gloss = "".join(f"<tr><td>{esc(k)}</td><td class='dim'>{esc(v)}</td></tr>"
                    for k, v in P.GLOSSARY.items())
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:#0b1220;color:#e2e8f0;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 10px;color:#94a3b8;
 border-left:3px solid #3b82f6;padding-left:10px}}
.sub{{color:#64748b;font-size:13px;margin-bottom:18px}}
.card{{background:#111a2e;border:1px solid #1e293b;border-radius:10px;padding:16px;margin-bottom:16px}}
.plain{{background:#0f1b33;border-left:3px solid #22c55e;padding:12px 14px;border-radius:6px;
 color:#cbd5e1;font-size:14px;margin-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:#64748b;font-weight:600;padding:8px 10px;border-bottom:1px solid #1e293b;
 white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #16203a;vertical-align:top}}
tr:hover td{{background:#16203a}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}}
.dim{{color:#64748b}} .warn{{color:#f59e0b}} .empty{{color:#475569;text-align:center;padding:20px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;
 border:1px solid transparent;white-space:nowrap}}
.tag.ctrl{{background:#a855f722;color:#a855f7;border-color:#a855f755}}
.tag.chan{{background:#47556922;color:#94a3b8;border-color:#47556955}}
.scroll{{overflow-x:auto}}
.note{{color:#64748b;font-size:12.5px;margin-top:22px;border-top:1px solid #1e293b;padding-top:12px}}
</style></head><body>
<h1>🚩 举牌行为监控 <span class="dim" style="font-size:14px">#17</span></h1>
<div class="sub">持股比例上穿 5% / 10% / 15% / 20% / 25% / 30% 梯度线的权益变动侦测 ·
 按<b>公告日</b> PIT 口径 · 报告期快照重建（非实时播报）</div>

<div class="card"><h2 style="margin-top:0">概览</h2>{_bar_svg(su)}
<div class="plain">{esc(su.get('plain_text', '（无数据）'))}</div></div>

<div class="card"><h2 style="margin-top:0">举牌事件</h2><div class="scroll"><table>
<thead><tr><th>公告日</th><th>代码</th><th>举牌方</th><th>性质</th><th>持股变动</th>
<th>越线</th><th>类型</th><th>意图倾向</th><th>锁定至</th></tr></thead>
<tbody>{rows_ev(ev)}</tbody></table></div>{_cap_note(len(ev), n_ev, "举牌类事件")}</div>

<div class="card"><h2 style="margin-top:0">逼近举牌线（观察名单）</h2><div class="scroll"><table>
<thead><tr><th>代码</th><th>股东</th><th>当前持股</th><th>下一条线</th><th>差距</th></tr></thead>
<tbody>{rows_wa(wa)}</tbody></table></div>{_cap_note(len(wa), n_wa, "逼近观察对象")}</div>

<div class="card"><h2 style="margin-top:0">通道账户越线 —— <span class="warn">不算举牌</span></h2>
<div class="sub" style="margin:0 0 10px">北向托管 / 回购专户 / 国家队等代持类账户，穿过 5% 无人申报权益变动，
仅作资金流向参考。</div><div class="scroll"><table>
<thead><tr><th>公告日</th><th>代码</th><th>账户</th><th>类别</th><th>持股变动</th></tr></thead>
<tbody>{rows_ch(ch)}</tbody></table></div>{_cap_note(len(ch), n_ch, "通道账户越线")}</div>

<div class="card"><h2 style="margin-top:0">术语表（学术 → 人话）</h2>
<table><tbody>{gloss}</tbody></table></div>

<div class="note">数据源 PandaData <code>get_top_holders</code>（十大股东快照，<code>stock_type=total</code> 口径）。
本口径为<b>报告期快照重建</b>，无法捕捉报告期之间发生并回撤的举牌；一致行动人无法合并（数据无该字段）。
Community Project，未经 QuantSkills 官方审核/认证/背书。<b>仅量化研究与教育示例，不构成投资建议，不承诺收益。</b></div>
</body></html>"""
