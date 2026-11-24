Date: 2026-11-24
Monthly research run, 2026-11-24 15:44.

Steps:
  Game situations: ok (11 min)
  Spread value tables: ok (5 min)
  NBA/NHL totals data: ok (0 min)
  Totals backtests: ok (2 min)
  BUY/SELL tag backtest: ok (3 min)
  Rating backtests: ok (10 min)
  Priced value end-to-end test: ok (4 min)
  Betting systems: ok (0 min)
  NHL systems: ok (0 min)
  CFB weather system: ok (0 min)
  EPL systems: ok (0 min)
  NBA season start file: ok (0 min)
  CFB model v2 data: ok (1 min)
  CFB model v2 record: FAILED (0 min) - KeyError: 'game_id'
  CBB model v2 record: ok (4 min)
  Live-model fair line shares: ok (0 min)
  Conference strength: ok (0 min)
  Slice test (Best spots): ok (0 min)
  CBB 3-point fade record: ok (0 min)
  Proven-rule audit (luck + recent seasons): ok (0 min)

Key numbers, last month -> this month:
  NFL spreads, share of gap that was real: 15% → 15%
  CFB spreads, share of gap that was real: 1% → 1%
  CBB spreads, share of gap that was real: 18% → 18%
  NBA spreads, share of gap that was real: -15% → -13%
  NFL totals, share of gap that was real: 13% → 13%
  CFB totals, share of gap that was real: 17% → 19%
  CBB totals, share of gap that was real: 26% → 22%
  EPL totals, share of gap that was real: -10% → -10%
  NBA totals, share of gap that was real: 30% → 28%
  NHL totals, share of gap that was real: -80% → -78%
  NFL BUY/SELL tags beat the close: 50% → 50%
  CBB BUY/SELL tags beat the close: 50% → 50%
  CFB BUY/SELL tags beat the close: 50% → 50%
  NFL ratings backtest, line moved toward our number: 64% → 64%
  EPL ratings backtest, line moved toward our number: 45% → 45%
  CFB ratings backtest, line moved toward our number: 54% → 55%
  CBB ratings backtest, line moved toward our number: 56% → 53%
  NHL ratings backtest, line moved toward our number: 62% → 62%
  NBA ratings backtest, line moved toward our number: 50% → 50%
  nfl spreads (Best spots rule, out of sample at the open): 0.569 of 248 (0.506–0.629), proven
  cbb spreads (Best spots rule, out of sample at the open): 0.496 of 1941 (0.474–0.518), not proven
  cbb_p4 spreads (Best spots rule, out of sample at the open): 0.498 of 291 (0.441–0.555), not proven
  nfl totals (Best spots rule, out of sample at the open): 0.606 of 33 (0.437–0.753), not proven
  cfb totals (Best spots rule, out of sample at the open): 0.512 of 383 (0.462–0.561), not proven
  cbb totals (Best spots rule, out of sample at the open): 0.525 of 455 (0.479–0.571), not proven
  cbb_p4 totals (Best spots rule, out of sample at the open): 0.578 of 83 (0.471–0.679), not proven
  nba totals (Best spots rule, out of sample at the open): 0.525 of 118 (0.436–0.613), not proven
  cfb_spread / gap 4+ (Best spots slice): 0.543 of 427 vs the opener (0.496–0.59), fails ← no longer passes: leaves Best spots
  cfb_spread / mid (Best spots slice): 0.556 of 1408 vs the opener (0.53–0.582), passes
  cfb_spread / favorite (Best spots slice): 0.54 of 607 vs the opener (0.501–0.58), passes ← NEW: joins Best spots
  cfb_spread / line under 7 (Best spots slice): 0.555 of 681 vs the opener (0.518–0.592), passes
  cfb_spread / conference game (Best spots slice): 0.558 of 1572 vs the opener (0.533–0.582), passes
  cfb_spread / Power 4 / Big East game (Best spots slice): 0.544 of 504 vs the opener (0.5–0.587), fails ← no longer passes: leaves Best spots
  System nfl_bye_edge: 0.543 of 556 vs the opener, passes
  System nfl_div_under: 0.531 of 1195 vs the opener, passes
  System nfl_high_total_under: 0.568 of 370 vs the opener, passes
  System nfl_wind_under: 0.608 of 250 vs the opener, passes
  System cbb_v2_open: 0.5417 of 384 vs the opener, fails ← no longer passes: tag removed
  System cbb_3p_fade: 0.5334 of 1331 vs the opener, passes

Game situations flagged:
  Students away (Dec 20-Jan 4): results +0.79 ± 0.28 (halves +1.03 / +0.65), close prices +0.37, close misses +0.42 ± 0.27; 2,039 games. CANDIDATE: clears the bar now; worth an out-of-sample check
  Finals weeks (Dec 6-19): results +0.94 ± 0.34 (halves +1.55 / +0.50), close prices +1.06, close misses -0.11 ± 0.32; 1,698 games. CANDIDATE: clears the bar now; worth an out-of-sample check
  Conference game (home edge): results +0.73 ± 0.23 (halves +1.49 / +0.07), close prices +1.25, close misses -0.40 ± 0.21; 13,393 games. CANDIDATE: clears the bar now; worth an out-of-sample check
  Crowd (per doubling): results +1.99 ± 0.28 (halves +3.29 / +1.22), close prices +1.87, close misses +0.12 ± 0.26; 3,530 games. IN USE (+1.15): the close now prices +1.87; consider re-pricing
  Denver home game: results +2.95 ± 1.15 (halves +2.72 / +3.06), close prices +0.57, close misses +2.37 ± 1.09; 120 games. CANDIDATE: clears the bar now; worth an out-of-sample check
