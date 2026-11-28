# DataPilot V2 · Stage 2C Offline Diagnostic (X08 / X03)

## Scope

This is **posthoc diagnostic work**, not a new first-attempt model run. The original X08 SQL and X03 SQL are loaded only from the first-run Hard8 `responses/` and checked against the saved SHA-256 manifest. No LLM is called, no prompt is changed, and all existing benchmark scores/Gold SQL remain untouched.

- X08: compare first-run model SQL, frozen Gold X08, and **manually authored filter-first** candidate under the **same VM/time limits**; show `EXPLAIN QUERY PLAN`, approximate executed VM steps, abort cause, timing, and equality with the stored Gold aggregate result.
- X03: augment a fresh disposable fixture with 2019-12 dates, including two unparseable non-NULL dates. Compare the original model SQL to frozen Gold X03 on this **new** adversarial dataset.

## Instructions (Windows PowerShell)

From the project root `D:\MyDemo\项目\datapilot-clean-check` with `.venv` active:

```powershell
python -m unittest discover -s tests -p "test_v2_stage2c_diagnostics.py" -v
python -m evaluation.v2_stage2c_diagnostics --mode all --run-dir ".\reports\agent_eval_v2_hard\20261127T094633Z_396342"
```

If X08 still aborts with the regular 5,000,000-step limit, to **diagnose only** under a separate, more generous 25-million-step/10-second limit:

```powershell
python -m evaluation.v2_stage2c_diagnostics --mode x08 --extended --run-dir ".\reports\agent_eval_v2_hard\20261127T094633Z_396342"
```

The `--extended` switch does **not** alter `app/v2_sql_policy.py` or the runtime executor budget. It runs local read-only diagnostics only. It can be more CPU-intensive. You can skip it if the ordinary run already identifies the issue.

Outputs: `reports/agent_eval_v2_stage2c/<UTC timestamp>_<id>/diagnostic.json`. Send that JSON for interpretation.

## Interpretation

- `interrupt_reason: "vm_step_budget"`: progress callback observed reaching its VM budget; this is **not** a wall-time timeout.
- `interrupt_reason: "wall_time_budget"`: observed time threshold.
- `vm_steps_lower_bound`: SQLite progress callbacks × interval; it is a **lower-bound estimate**, not exact VM opcode count. The time measure excludes SQL parsing and plan generation.
- `plan[].detail`: SQLite `EXPLAIN QUERY PLAN` output; inspection and runtime statistics together are needed to justify optimizations.
- `gold_comparison.match: true`: result matches the previously frozen real-data X08 Gold output, across all months, order counts, and both amounts. If `null`, the query did not complete.
- `x03.divergence: true`: original query is vulnerable to the adversarial date corpus; this is not an update to the frozen Hard8 score.

## Safety and reproducibility

- All profile connections use URI read-only mode, `PRAGMA query_only=ON`, the existing Stage 2A authorizer, and the existing Stage 2A SQL validator.
- The separate filter-first candidate is not offered to the LLM, not automatically deployed, and not counted as an LLM repair success.
- No production Olist data are copied to synthetic fixtures; only aggregate rows and query plans are saved.
- Verify existing `run_manifest.json`, frozen case/Gold SHA-256, original response SHA-256, and source database SHA-256 before diagnosis. The original reports are not overwritten.
- The project remains a **local trusted SQLite analytics prototype**, not a hardened multi-tenant service. This diagnostics script is not a performance test under controlled repeated trials; cached pages/index presence can affect timing.
