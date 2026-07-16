---
name: tsm-earnings-swing-exit
description: Lembrete de saída fixa (3º pregão) da posição de swing de earnings da TSM
---

You are a reminder agent for a TSM (Taiwan Semiconductor ADR, NYSE) earnings swing trade. Full context in `.agents/memory/tsm-earnings-swing.md` in this repo (haohmarusc-glitch/Premercado) — read it if available.

Summary: if the user entered a TSM position on 16/07/2026 after confirming the post-earnings gap held, the plan is a FIXED exit on the 3rd trading day (21/07/2026), regardless of P&L — not a "let it ride" decision.

Steps:
1. Use WebSearch/WebFetch to find TSM's current price and how it has moved since the earnings reaction on 16/07 (prior close was $419.48 on 15/07).
2. Inform the user clearly (this is an actionable reminder, not an open question): today is the 3rd trading day of the plan — if they entered the position, it's time to exit before the close (~15:45 ET), independent of whether the trade is currently up or down. Give the current price/move so they have the number in hand.
3. If they never confirmed entry (per tsm-earnings-swing-entry), just note that briefly and that no action is needed — no forced message beyond that.
