"""PandaData → 举牌监控标准面板（#17）。

数据现实（真机实测，勿凭清单臆断）：
- 任务清单点名的 `get_stock_equity_placard` **在 PandaData 最新接口文档（187 方法）中不存在**，
  文档里连"举牌/权益变动/一致行动"字样都没有。举牌事件由 `get_top_holders` 的
  **十大股东持股比例快照**重建：同一股东的持股比例在相邻报告期之间**上穿 5% 及其梯度**即为举牌。
- `get_top_holders` 同时返回 `stock_type ∈ {total, flow}` 两套行：
  * `total` = 十大股东（持有全部已发行 A 股口径）→ **举牌的法定口径，必须用这套**
  * `flow`  = 十大流通股东（只算流通部分）→ 其 `hold_percent_total` 是在流通股本上算的，
    与总股本口径**不同名同值**。实测格力电器持盾安环境 20241231：
    `flow` 行读 25.38%、`total` 行读 38.46%（公开事实约 38%）。用错行整个举牌梯度全错。
- 一个 `date`（公告日）下可能混着**多个 `end_date`（报告期）**，必须按 (股东, 报告期) 去重。

凭证一律走 PANDA_USERNAME / PANDA_PASSWORD 或 ~/.pandadata/pandadata.env，绝不硬编码。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# ============================================================================
# 主体分类：通道/代持类账户不是举牌人（本 skill 最大的假阳性来源）
# ============================================================================
# 实测：全市场一年半的"上穿 5%"事件里，最高频的主体是「香港中央结算有限公司」——
# 那是沪深股通的**名义持有人（北向托管）**，背后是成千上万境外投资者，没有任何人
# 交过权益变动报告书。把它当举牌播报是硬伤，故单独归类。
CHANNEL_PATTERNS: dict[str, list[str]] = {
    "北向托管": ["香港中央结算有限公司", "香港中央结算(代理人)", "香港中央结算（代理人）"],
    "回购专户": ["回购专用", "回购专户"],
    "国家队": ["中国证券金融股份有限公司", "中央汇金", "中国国际金融股份有限公司-中国证券金融"],
    "结算/登记": ["中国证券登记结算", "证券投资者保护基金"],
}

# 真实举牌人的类别画像（用于"财务 vs 战略"意图判断的先验）
STRATEGIC_HINTS = ["集团", "控股", "投资控股", "实业", "产业投资"]
FINANCIAL_HINTS = ["基金", "资产管理", "资管", "信托", "保险", "年金", "社保", "QFII", "私募"]


def classify_holder(name: str, kind: str = "", attr: str = "") -> str:
    """把股东名归成 举牌人 / 通道账户 类别。

    通俗：先认出"这到底是不是一个真会举牌的人"——北向托管账户和公司自己的回购专户
    持股穿过 5% 都不叫举牌，得先摘出去。
    """
    n = str(name or "")
    for cls, pats in CHANNEL_PATTERNS.items():
        if any(p in n for p in pats):
            return cls
    return "举牌人"


def is_channel(name: str) -> bool:
    return classify_holder(name) != "举牌人"


def holder_nature(name: str, kind: str = "", attr: str = "") -> str:
    """举牌人性质（供意图画像）：自然人 / 产业资本 / 金融资本 / 其他机构。

    通俗：来的是"想进董事会的产业方"还是"只想赚钱的资管产品"，打法完全不同。
    """
    n, k = str(name or ""), str(kind or "")
    if "自然人" in k or (len(n) <= 4 and not any(c in n for c in "公司基金计划组合")):
        return "自然人"
    if any(h in n for h in FINANCIAL_HINTS) or any(h in k for h in ("基金", "保险", "信托", "资管")):
        return "金融资本"
    if any(h in n for h in STRATEGIC_HINTS):
        return "产业资本"
    return "其他机构"


# ============================================================================
# 拉数
# ============================================================================
def _read_env_file() -> dict:
    """读 ~/.pandadata/pandadata.env（官方文件用 DEFAULT_USERNAME/DEFAULT_PASSWORD 键名）。"""
    env: dict[str, str] = {}
    p = Path.home() / ".pandadata" / "pandadata.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def init_panda() -> Any:
    """初始化 panda_data。凭证优先环境变量，回退官方 env 文件；**绝不硬编码**。"""
    try:
        import panda_data
    except ModuleNotFoundError as exc:
        raise RuntimeError("无法导入 panda_data，请先 `pip install --upgrade panda_data`（需 ≥0.0.9）") from exc
    envf = _read_env_file()
    user = os.getenv("PANDA_USERNAME") or os.getenv("PANDA_DATA_USERNAME") or envf.get("DEFAULT_USERNAME", "")
    pwd = os.getenv("PANDA_PASSWORD") or os.getenv("PANDA_DATA_PASSWORD") or envf.get("DEFAULT_PASSWORD", "")
    base = os.getenv("PANDA_BASE_URL") or envf.get("JAVA_SERVICE_BASE_URL")
    if not (user and pwd):
        raise RuntimeError("缺少 PANDA 凭证（环境变量 PANDA_USERNAME/PANDA_PASSWORD 或 ~/.pandadata/pandadata.env）")
    if base:
        panda_data.init_token(username=user, password=pwd, base_url=base)
    else:
        panda_data.init_token(username=user, password=pwd)
    return panda_data


def load_top_holders(api: Any, symbol: str = "", start: str = "", end: str = "") -> pd.DataFrame:
    """拉十大股东快照并归一化成标准面板。

    返回列：symbol / announce_date（公告日，PIT 基准）/ as_of_date（报告期截止日）/
            holder_name / pct（占总股本 %）/ rank / holder_kind / holder_attr /
            holder_class（举牌人 or 通道类）/ nature（举牌人性质）
    """
    df = api.get_top_holders(symbol=symbol or "", start_date=start, end_date=end, fields=[])
    return normalize_holders(df)


def normalize_holders(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """把 get_top_holders 原始返回归一化（纯函数，零 IO —— 供离线测试）。"""
    cols = ["symbol", "announce_date", "as_of_date", "holder_name", "pct",
            "rank", "holder_kind", "holder_attr", "holder_class", "nature"]
    if df is None or len(df) == 0:
        empty = pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
        empty["pct"] = pd.Series(dtype="float64")
        empty["rank"] = pd.Series(dtype="float64")
        return empty

    d = df.copy()
    # 【关键】只取 total 口径：flow 行的 hold_percent_total 是在流通股本上算的，非举牌口径
    if "stock_type" in d.columns:
        d = d[d["stock_type"].astype(str).str.lower() == "total"]
    if d.empty:
        return normalize_holders(None)

    out = pd.DataFrame({
        "symbol": d.get("symbol", "").astype(str),
        "announce_date": d.get("date", "").astype(str),
        "as_of_date": d.get("end_date", "").astype(str),
        "holder_name": d.get("holder_name", "").astype(str).str.strip(),
        "pct": pd.to_numeric(d.get("hold_percent_total"), errors="coerce"),
        "rank": pd.to_numeric(d.get("rank"), errors="coerce"),
        "holder_kind": d.get("holder_kind", "").astype(str),
        "holder_attr": d.get("holder_attr", "").astype(str),
    })
    out = out.dropna(subset=["pct"])
    out = out[(out["holder_name"] != "") & (out["symbol"] != "")]
    # 同一 (票, 股东, 报告期) 可能因公告日不同重复出现 → 保留公告日最早的一条（首次披露即 PIT 时点）
    out = (out.sort_values(["symbol", "holder_name", "as_of_date", "announce_date"])
              .drop_duplicates(subset=["symbol", "holder_name", "as_of_date"], keep="first"))
    out["holder_class"] = out["holder_name"].map(classify_holder)
    out["nature"] = [holder_nature(n, k, a) for n, k, a in
                     zip(out["holder_name"], out["holder_kind"], out["holder_attr"])]
    return out.reset_index(drop=True)


def chunk_pull(api: Any, symbols: list[str], start: str, end: str,
               batch: int = 50) -> pd.DataFrame:
    """按批拉多票（单票接口逐个调用较慢时用；symbol='' 可一次拉全市场）。"""
    frames = []
    for i in range(0, len(symbols), batch):
        for s in symbols[i:i + batch]:
            try:
                frames.append(load_top_holders(api, symbol=s, start=start, end=end))
            except Exception:  # noqa: BLE001 单票失败不拖垮整批
                continue
    if not frames:
        return normalize_holders(None)
    return pd.concat(frames, ignore_index=True)
