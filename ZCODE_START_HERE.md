# ZCode Start Here

Primary handoff document:

`docs/strategy-compiler/zcode-handoff-20260721.md`

Read it before changing runtime code. Then run:

```powershell
python -m pytest -q
python scripts/run_strategy_fidelity_gates.py
```

Expected current baseline:

```text
255 passed
ETF momentum: PASS
small-cap guard: CLOSE within the frozen envelope
```

Current project stage: PR1 implementation and Fidelity hardening are complete; PR2 has not started. Do not start PR2 until the user confirms the PR1 handoff state. PR2 must be limited to true `next_open` pending-order semantics.
