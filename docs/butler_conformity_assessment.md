<!-- SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
SPDX-License-Identifier: GPL-3.0-only -->

# pysewer vs. Butler & Davies — foul sewer design conformity assessment

Assessed 2026-08 against: Butler, Digman, Makropoulos & Davies, *Urban
Drainage*, Chapter 9 "Foul sewers" (design of large and small foul sewers,
BS EN 752 / Sewers for Adoption practice). Also compared against the
experimental sizing work in the `pysewer-storm` repo
(`pysewer/hydraulics/formulae.py`).

## 1. Butler's design chain (what "spot on" means)

1. **Dry weather flow**: `DWF = P·G + I + E` — population × per-capita
   consumption **+ infiltration + commercial/industrial** (Eq. 9.1).
2. **Peak flow**: DWF × peak factor. Two accepted approaches:
   - fixed multiple (BS EN 752: up to **6** for small sub-catchments, **4**
     for larger sewers, 2.5 for combined-sewer DWF);
   - **variable peak factor decreasing downstream** with cumulative
     population (attenuation + diversification): Harman, Babbitt
     `PF = 5/P^0.2`, Gifft, Gaines (Table 9.4, Fig. 9.5).
3. **Capacity**: choose size/gradient so the peak flow is conveyed at
   proportional **depth** d/D ≤ 0.75 (ventilation); small sewers d/D < 0.7.
4. **Self-cleansing**: minimum velocity 0.7 m/s (BS EN 752, ≤ DN300),
   or Sewers-for-Adoption 0.75 m/s at 2·DWF ("once per day"); at heads of
   runs, velocity criteria are *replaced* by deemed-to-satisfy minimum
   gradients (Table 9.8: ≥1:80 at DN100/1 WC, ≥1:150 at DN150/5 WCs).
5. **Roughness**: Colebrook–White with k_s = 0.6 mm (peak DWF vel > 1 m/s)
   or 1.5 mm (0.76–1 m/s), independent of pipe material (slimed).
6. **Minimum pipe sizes**: DN75–100 house drains; DN100–150 upper public
   reaches — minimums come from solids/maintenance, not capacity.
7. **Small sewers** (heads; intermittent flow, `N·p < 1`): probabilistic
   **Discharge Unit method**, `Q = k_DU·√(ΣDU)` — not steady per-capita
   flow.

## 2. What pysewer does today (current `main`)

- Peak flow: `Q = P·G·PF/86400` with **flat PF = 4.0** applied identically
  on every edge; population from buildings (count × inhabitants, or the
  `population` attribute). **No infiltration, no commercial/industrial
  terms.**
- Sizing: smallest listed diameter with `Q_peak/Q_full ≤ max_depth_ratio`
  (default 0.75), full-bore Manning capacity, slope = designed trench
  gradient; monotonic non-decreasing downstream. Velocity evaluated at
  partial flow (correct wetted-angle bisection) and **recorded** as
  `velocity_min`/`velocity_max` violations, never used to inflate size.
- Roughness: Manning *n*, default 0.013 (fixed 2026-08-06; was a k_s value
  misused as *n*).
- Minimum diameter: implicit floor of the configured diameter list
  (default 0.2 m).

## 3. Conformity, item by item

| # | Butler element | pysewer main | Verdict |
|---|---|---|---|
| 1 | DWF = PG + I + E | PG only | **Gap** — no infiltration/industrial allowance |
| 2 | PF varies with population (6 heads → ~2 large) | flat 4.0 | **Gap** — see §4.2 |
| 3 | Capacity at proportional depth d/D ≤ 0.75 | flow ratio Q/Q_full ≤ 0.75 | **Deviation + mislabel** — see §4.1 |
| 4 | Self-cleansing 0.7 m/s, deemed-to-satisfy at heads | 0.7 m/s flag at design peak on *every* edge | **Partial** — heads over-flagged; see §4.3 |
| 5 | Colebrook–White, k_s 0.6/1.5 mm | Manning n = 0.013 | **Acceptable** — n 0.013 ≈ k_s 1.5 mm for DN150–600; CW optional later |
| 6 | Min diameter DN100–150 by practice | list floor (0.2 default) | **Conforms** (config); document DN150 as sensible public floor |
| 7 | DU method for intermittent heads | steady per-capita everywhere | **Gap (known limitation)** — see §4.3 |
| 8 | d/D ≤ 0.75 restriction to ensure ventilation | 0.75 *flow* ratio ≈ d/D 0.65 | Conservative — never unsafe, slightly oversizes trunks |
| 9 | Gradient as design variable (size+gradient manipulated) | gradient from terrain/trench design; size only | **By design** — pysewer is layout-first; acceptable, but self-cleansing must then be honest (see §4.3) |

## 4. The three deviations that matter

### 4.1 `d_over_D` is a flow ratio, not a depth ratio (P0 — reporting correctness)

pysewer's capacity check and the exported `d_over_D` attribute are
`Q_peak/Q_full`, a **proportional discharge**. Butler's criterion is
proportional **depth**. They are related but numerically different for
circular pipes:

- Butler's d/D ≤ 0.75 ⟺ Q/Q_full ≤ **0.912**;
- pysewer's Q/Q_full ≤ 0.75 ⟺ d/D ≈ **0.646**.

Consequences: (a) the exported attribute misstates the physical quantity
users think they are reading (Elan read it as depth); (b) sizing is
~stricter than Butler — never unsafe, but trunks step up one diameter
earlier than BS EN practice would.

**Fix**: compute true proportional depth from the wetted angle (the
bisection already exists in `_partial_flow_velocity`), export it as
`d_over_D`, and implement the capacity criterion as Butler does:
`Q_peak ≤ Q(at d/D = max_depth_ratio)`. `pysewer-storm` already has the
right building block — `mannings_equation(..., fill_ratio)` computes
capacity at a given proportional depth with exact geometry; port it.
With `max_depth_ratio: 0.75` the criterion then means what Butler means.

### 4.2 Flat peak factor (P1 — accuracy of design flows)

Butler is explicit that PF decreases downstream (diversification +
attenuation). Reference values at relevant populations:

| population | Babbitt 5/P^0.2 | Harman | pysewer flat |
|---|---|---|---|
| 10 | 12.6 (cap ~6) | 4.4 | 4.0 |
| 500 | 5.7 | 4.0 | 4.0 |
| 1,100 (Elan catchment) | 4.9 | 3.8 | 4.0 |
| 100,000 | 2.0 | 2.0 | 4.0 |

For pysewer's typical small rural catchments flat 4.0 is defensible
(Harman-like), but: heads are underestimated vs BS EN's 6, and any larger
catchment is systematically overestimated (~2× at 100k). Since cumulative
population is already tracked per edge, a per-edge
`PF(P) = min(PF_max, a/P^b)` is cheap.

**Fix**: config `peak_factor_method: constant | babbitt | harman | gifft`
(+ `peak_factor_max: 6.0` cap per BS EN 752), default `constant` for
backward compatibility, recommend `babbitt` in docs. Also add optional
`infiltration_fraction` (Butler's conventional 10% of DWF) closing gap #1.

### 4.3 Self-cleansing at heads of runs (P1 — honesty of the violation flags)

At network heads the steady-flow abstraction is wrong (Butler §9.4: flow is
intermittent pulses; `N·p < 1`); computing a partial-flow velocity from a
per-capita trickle there produces physically meaningless values — this is
why ~30% of edges carry `velocity_min` flags on our runs. Butler's
practice: at heads, replace the velocity criterion with deemed-to-satisfy
minimum gradients (Table 9.8), relying on the WC flush wave.

**Fix**: suppress `velocity_min` on edges where the design flow is below a
threshold (Butler: Q < 1 L/s ≈ small-sewer regime) *and* the gradient
satisfies the deemed-to-satisfy rule (≥1:80 for ≤DN100 with ≥1 WC; ≥1:150
for DN150 with ≥5 dwellings); flag `gradient_below_dts` otherwise. Optional
later: full DU method (`Q = k_DU·√(ΣDU)`, ~3.5 DU/dwelling) as an
alternative head-flow model — this is also the technically correct answer
to "why are head velocities so low".

## 5. Cross-repo notes (pysewer-storm)

Worth porting back:
- `hydraulics/formulae.py::mannings_equation(fill_ratio)` — exact
  partial-depth capacity (fixes §4.1).
- `enforce_global_monotonic_diameters` — main enforces monotonicity only
  along traversal; a global pass is a useful invariant check.
- `design_hydraulics_with_fallback` (method ranking) — overkill for main
  today, but the `validate_hydraulic_constraints` post-check pattern is
  good hygiene.

Not to port: the pysewer-storm "guo"-method machinery (storm/combined
specific).

## 6. Verdict

pysewer's sizing chain is **structurally Butler-conform** (peak flow from
population → capacity-constrained smallest diameter → monotonic downstream
growth → velocity as reported check, minimum-diameter governed at small
scale — the last being *correct*, not a bug). After the roughness fix the
hydraulic numbers are physically sound. It is **not yet "spot on"** on
three points: the depth/flow-ratio mislabel (P0), the flat peak factor and
missing infiltration (P1), and spurious self-cleansing flags at intermittent
heads (P1). All three have well-defined, cheap fixes; §4 gives the exact
specifications.
