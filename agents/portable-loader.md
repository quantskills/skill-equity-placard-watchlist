# Portable Loader Prompt / 便携加载提示

This file is the portable entrypoint for Hermes, OpenClaw, and any agent runtime that does not
natively discover `SKILL.md` folders. Native runtimes (Claude Code, Codex, Cursor) should read
[SKILL.md](../SKILL.md) directly; the Cursor rule is [cursor-rule.mdc](cursor-rule.mdc) and the
Codex interface is [openai.yaml](openai.yaml).

本文件是 Hermes / OpenClaw 等不原生识别 `SKILL.md` 目录的运行时的便携入口。
把下面这段提示粘贴进运行时的系统提示或工具描述，并把占位路径替换成仓库根目录。

```text
You have access to a local skill named skill-equity-placard-watchlist at:
<SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>

Use it when the user asks who is placarding an A-share (crossing 5%/10%/15%/20%/25%/30% disclosure thresholds), at which tier, financial vs strategic intent, or which holders are approaching the 5% line.

1. Read <SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>/SKILL.md first; it is the canonical declaration.
2. Read <SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>/开发产物/SKILL.md for the agent-facing interface (inputs, outputs, run()/validate_input()).
3. Read the references before touching data logic:
   - <SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>/开发产物/references/api_guide.md
   - <SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>/开发产物/references/quality_evidence.md
4. Run entrypoints from <SKILL_EQUITY_PLACARD_WATCHLIST_ROOT>:
   python 开发产物/scripts/build.py --mode scan --start 20250101 --end 20260721 --save --html board.html
   python 开发产物/scripts/build.py --symbols 601005.SH 002011.SZ --start 20240101 --end 20260721
   python 开发产物/scripts/test.py
5. Use only `stock_type='total'` rows for holding ratios; `flow` rows are on float-share basis and corrupt the tier ladder
6. Passive dilution from share issuance is not a sell-down: infer the share-change factor per (symbol, report period) first and only count trade-driven crossings
7. Results are report-period snapshot reconstructions, not real-time alerts; acting-in-concert parties cannot be merged; keep these limits in the output
8. Credentials come only from PANDA_USERNAME / PANDA_PASSWORD or ~/.pandadata/pandadata.env; never hard-code or print them.
9. This is a QuantSkills Community Project for research and education only: no investment advice, no return promises, no claim of official endorsement.
```
