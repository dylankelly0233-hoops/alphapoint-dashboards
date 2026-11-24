# Build the 2027 college football model (v3) — run once, summer 2027

Dylan approved this on 2026-11-24: for the 2027 season, the CFB model moves from v2 to "v3" — v2 plus more stats (points per drive, power success, second-level and open-field yards, sack rate, rushing and passing success rate) and separate stage-2 weights for weeks 1-4. In the 2026 walk-forward test (`cfb_model_v3_test.py`) it lowered the average miss over all weeks (2024-26 holdout 13.10 -> 12.96) and tied from week 5 on, so it helps early season. `cfb_model.py` already supports it: v3 switches on when `cfb_v2_params_2027.json` has `stats` and `stage2_early` keys (checked 2026-11-24: live code matches the research within 0.005 pts; v2 output unchanged).

When: after CFBD publishes 2027 returning production and talent (usually by July). Mind the CFBD call limit (this is about 30 calls). Never print or commit the CFBD key. Do not commit until Dylan has read the report.

1. Refresh the full 2026 season in the research cache: delete `research_cache/cfbm_2026.json`, `cfbm_adv_2026.json`, `cfbm_box_2026.json`, then run `python cfb_model_fetch2.py` (re-pulls missing years). Check the latest 2026 game is the January 2027 title game.
2. `python cfb_model_v3_test.py` — the pre-set rule must still print PASS with the full 2026 season. If FAIL, stop and report the table.
3. `python cfb_model_v3_test.py --params 2027` — writes `cfb_v2_params_2027.json` (2 CFBD calls for returning production and talent; it stops if they aren't published yet).
4. Sanity check: version `v3`, `trained_on` ends at 2026, about 136 teams in the preseason start; the top 15 by `prior['margin']` should look like a reasonable preseason top 25.
5. Report to Dylan, with two decisions: (a) commit the file so the site runs v3 from week 1; (b) the Best spots strategy "AlphaPoint CFB model 3+ pts from the opener (week 5+)" uses `cfb_model.line`, so committing v3 also moves that strategy onto v3 (2026 test: 55.3% vs the opener on v3, low end 52.6%, vs 55.5% on v2). Changing a Best spots strategy's basis needs Dylan's explicit OK.
