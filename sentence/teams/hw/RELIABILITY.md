# How reliable is learning a new part? (실기기팀 5차, 2026-10-12)

Every trial learned one part **from nothing** through `learn.start()` (the job the hub runs), on an
**isolated copy** of the fragment catalog and `incubated/` (the part removed from the copy first).
Owner catalog / fragments / incubated / records: **unchanged** (digest before = after, every run).
Harness: `teams/hw/t_reliability.py`, raw data `results/reliability.json`. Model calls: **≈ 32** (budget ~30).

## Results

| part (twin) | trials | learned | rounds | time (s) | model calls | identified as | identify() | write permissions after V7 review |
|---|---|---|---|---|---|---|---|---|
| TMP421 temperature | 3 | **3/3** | 1, 1, 1 | 81, 99, 81 | 2, 2, 2 | TI TMP421 (0.93–0.95) | id ×3 | none (read-only) ×3 |
| PCA9554 GPIO | 3 | **3/3** | 1, 1, 2 | 105, 105, 295 | 2, 2, 3 | PCA9554/TCA9554 (0.85–0.90) | id ×3 | `set_pin` regs [1, 3], max 2, confirm — **identical ×3** |
| DPS310 pressure | 3 | **3/3** | 2, 1, 1 | 247, 217, 187 | 4, 3, 3 | DPS310 (0.70–0.85) | id ×3 | none ×3 (see risk 1) |
| EMC1413 (never learned before) | 1 | **1/1** | 2 | 162 | 3 | "EMC1403" (0.85) | id | none (read-only) |
| AT24C02 EEPROM (negative control) | 1 | **1/1 — registered** | 3 | 406 | 4 | 24C02-compatible (0.80) | id | `write` regs [0x60…0xE0], max 33, confirm |

Totals: **11/11 learned**, 1–3 rounds (mean 1.5), 81–406 s (median 162 s), 2–4 model calls per part.
(An earlier EMC1413 trial was cancelled midway by a concurrent regress job that used the same address — fixed:
`t_learn.py` now uses 0x19; not counted.) Power-on counts are approximate (the identification agent's probes
are counted from the shared probe log, which other teams' runs also append to): 21–200 per trial.

## What is consistent and what is not

- **Permissions are consistent**: for every part with a write function the V7 review gave the same write
  registers, max_write and policy in every trial (PCA9554 ×3). Read-only parts stayed read-only ×3.
- **Identification is consistent** (same chip every time; confidence varies 0.70–0.95).
- **The interface is not**: function names and signatures differ between trials of the same part
  (`tmp421_read_temp(addr, ch, *mc)` / `tmp421_read(addr, *local, *remote, *open)`). Ops written for one
  learning would not compile against another — a relearned part needs its sentences re-pronounced. Proposal:
  when a part is relearned, give the writer the previous fragment's signatures and require them.
- **Round 2 when it happens** comes from a real defect found by the hub: one trial's identify() said "this
  part" at three other addresses (the EEPROM; caught by the automatic negative control), a units slip
  (bench knob drawn in °C for a m°C property), a knob that cannot be set after power-on.

## Risks found

1. **DPS310: the review is consistently wrong for a real chip.** The twin's DPS310 is already configured at
   power-on, so no trial's fragment was ever seen writing its configuration registers; V7 keeps only what was
   seen → `write_regs: []` in all three trials. On a real chip the first configuration write would be refused.
   This is the "cold start" gap (4th wave): until the twin's model finishes a measurement from cold, DPS310
   needs an exception or a real chip. The owner's registered DPS310 keeps its declared access (review pending).
2. **identify() can claim more than it knows.** On the twin the DPS310's product-ID register reads 0xFF, yet all
   three identify() functions returned "id" — they recognise the register layout / coefficients, not the ID.
   The EEPROM identify works by behaviour (contents pattern), which any memory part would share. Proposal:
   identify() also reports its basis (ID register / layout / behaviour) and V7 treats only "ID register"
   as identity for write permission.
3. **V7 least privilege can make a write function partly unusable**: the EEPROM's write function lost the
   block offsets its tests never wrote (0x00, 0x20, 0x40) — writing at offset 0 would be refused. Correct by
   the rule, surprising to a person; tests should cover the whole declared range, or the review should
   name the coverage gap as a warning the hub shows.

## The negative control did not behave as expected — and that is a finding

The EEPROM was chosen as a part "with nothing to verify it by". It was **registered** (read allow, write
confirm). On reflection the hub did not lie: a write-then-read-back on the twin *is* evidence (the device kept
the bytes), and the review limited writes to what was seen and to "confirm". What the hub cannot know is
*which* memory part it is (24C02 vs any 256-byte EEPROM/FRAM), and its identify() is behavioural. So the
right control for "unverifiable" is a part whose state cannot be read back at all (e.g. an SSD0303-like
write-only part with no screen on the bench) — next run. For now: the hub registers what it can verify, and
says how it verified it (`verification_kind` in each record).

## Fixes made before this run (verify team's audit, `teams/verify/t_learn.py` 13/14)
probe identification mode (target addresses only, write caps, never a write to a known part); one learning at a
time (queued) with per-job work directories; half-finished registrations taken back at start
(`reconcile_orphans`); verification images built with `build(allow=)`; an already-known address is not
relearned unless `--relearn`; a model block (POKE_CLAUDE) reaches the job's process.
