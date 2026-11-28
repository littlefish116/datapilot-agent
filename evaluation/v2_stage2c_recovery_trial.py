"""Offline, fail-closed X08 recovery CANDIDATE trial (not a general optimizer).

Takes the original frozen first-run response and a manually vetted filter-first
candidate. Never regenerates SQL, never raises security budgets, never modifies
original evaluation reports, never calls DeepSeek, never changes V1/V2 runtime.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from evaluation.v2_stage2c_diagnostics import (
    DB_DEFAULT, FILTER_FIRST, RUN_DEFAULT, ROOT,
    compare_x08, read_json, sha256_file, verified_first_sql,
)
from app.v2_semantic_risk_guard import inspect_semantic_risks

OUT_DEFAULT = ROOT / 'reports/agent_eval_v2_stage2c_recovery'

# Digests of validated SQL from the original 2026-11-27 X08 diagnostics.
# These are *deliberately* pinned to this historical case, not a broad rewrite
# policy. Any SQL change must undergo separate review before re-enabling it.
PINNED_FIRST_SQL_SHA256 = '92ebd636006200b5787b07735c1c6e47b41a5209ddd8a87f4e5ad2866e4a3f2e'
PINNED_CANDIDATE_SHA256 = 'aaf4985c6c7008e77874fe4565922bc6fb9edfaed1988f75a25fc611fd87f457'
PINNED_RESPONSE_SHA256 = '58a2a09a393ea667382e8120eddf045a88daa718109877a714f0c323bc1cf7bb'
PINNED_SUITE_SHA256 = 'c37d6685bbc1a1a701ebd840425b53d89c3f809940b38c892a7a8cd8ea6c84ef'
PINNED_DB_SHA256 = '8ccdc052bb039777633bb01472773749a41af280c52c8991d680a02a8730c60f'
PINNED_FIXTURE_SHA256 = '98c047975c72a8901e278a97c2dbd1203e4a796690ae044d263c10e15b55f8fd'
PINNED_GOLD_REAL_SHA256 = '01461bd3f20eaea248f5c51ed92549ad04eea51000cacb3057c8d17db109a606'
PINNED_GOLD_EDGE_SHA256 = 'dd9c1d82bd4df6034f69c4fe2acb21a6f98f44732211f97e507c3f4f1497e803'


def _digest_sql(normalized_sql: str) -> str:
    return hashlib.sha256(normalized_sql.encode('utf-8')).hexdigest()


def verified_recovery_inputs(
    run_dir: Path, db_path: Path, *, candidate_path: Path = FILTER_FIRST,
    validator=None,
) -> dict:
    """Verify provenance, failure type, DB identity, candidate identity."""
    if validator is None:
        from app.v2_safe_sql import validate_sql
        validator = validate_sql
    run_dir = Path(run_dir).resolve()
    db_path = Path(db_path).resolve()
    orig = verified_first_sql(run_dir, 'X08')
    resp = read_json(run_dir / 'responses/X08.json')
    if sha256_file(run_dir / 'responses/X08.json') != PINNED_RESPONSE_SHA256:
        raise ValueError('X08 first-run raw response differs from the reviewed snapshot')
    execution = resp.get('execution')
    if not (resp.get('status') == 'error' and isinstance(execution, dict)
            and execution.get('status') == 'error'
            and (execution.get('error') or {}).get('code') == 'query_timeout'):
        raise ValueError('Frozen X08 must be the original query_timeout failure')
    manifest = read_json(run_dir / 'run_manifest.json')
    if (manifest.get('hashes') or {}).get('suite_sha256') != PINNED_SUITE_SHA256:
        raise ValueError('Frozen suite differs from reviewed case')
    if manifest.get('db_sha256') != PINNED_DB_SHA256:
        raise ValueError('Frozen DB differs from reviewed case')
    if sha256_file(db_path) != manifest.get('db_sha256'):
        raise ValueError('Real Olist database hash differs from frozen first run')
    fixture = run_dir / 'synthetic_fixture.db'
    if manifest.get('fixture_sha256') != PINNED_FIXTURE_SHA256 or sha256_file(fixture) != PINNED_FIXTURE_SHA256:
        raise ValueError('Original synthetic fixture hash differs')
    candidate_path = Path(candidate_path).resolve()
    if candidate_path != FILTER_FIRST.resolve():
        raise ValueError('Only the reviewed project-local candidate is supported')
    candidate = candidate_path.read_text(encoding='utf-8-sig')
    if _digest_sql(validator(orig)) != PINNED_FIRST_SQL_SHA256:
        raise ValueError('Unrecognized first-run SQL; recovery is not permitted')
    if _digest_sql(validator(candidate)) != PINNED_CANDIDATE_SHA256:
        raise ValueError('Candidate SQL differs from the reviewed version')
    for label, pinned_hash in (('gold_real', PINNED_GOLD_REAL_SHA256),
                               ('gold_edge', PINNED_GOLD_EDGE_SHA256)):
        if sha256_file(run_dir / label / 'X08.json') != pinned_hash:
            raise ValueError('Stored Gold output differs from reviewed first-run snapshot')
        gold = read_json(run_dir / label / 'X08.json')
        if gold.get('status') != 'ok' or not isinstance(gold.get('rows'), list):
            raise ValueError('Frozen Gold output is missing or invalid')
    return {
        'original_sql_sha256': PINNED_FIRST_SQL_SHA256,
        'candidate_sql_sha256': PINNED_CANDIDATE_SHA256,
        'original_error_code': 'query_timeout',
        'candidate_sql': candidate,
        'fixture_path': fixture,
    }


def trial_verified_candidate(
    run_dir: Path, db_path: Path, *, executor=None, validator=None,
) -> dict:
    """Execute a fixed reviewed candidate under the unchanged Stage 2A limits."""
    if executor is None:
        from app.v2_safe_sql import execute_safe_sql
        executor = execute_safe_sql
    plan = verified_recovery_inputs(run_dir, db_path, validator=validator)
    candidate = plan.pop('candidate_sql')
    fixture = plan.pop('fixture_path')
    run_dir = Path(run_dir).resolve()
    trials = {}
    for label, database in (('real', Path(db_path)), ('edge', fixture)):
        execution = executor(candidate, database)
        if not isinstance(execution, dict) or execution.get('status') != 'ok' or execution.get('truncated'):
            trials[label] = {
                'status': 'execution_failed',
                'error': execution.get('error') if isinstance(execution, dict) else None,
                'gold_comparison': {'match': None, 'reason': 'candidate_did_not_complete'},
            }
            continue
        gold = read_json(run_dir / f'gold_{label}/X08.json')
        comparison = compare_x08(execution.get('rows', []), 'filter_first', gold['rows'])
        trials[label] = {
            'status': 'ok',
            'returned_rows': execution.get('returned_rows'),
            'duration_ms': execution.get('duration_ms'),
            'gold_comparison': comparison,
        }
    passed = all(x['status'] == 'ok' and x['gold_comparison']['match'] is True
                 for x in trials.values())
    return {
        'kind': 'posthoc_manual_candidate_recovery_trial',
        'source_run': run_dir.name,
        'provenance': plan,
        'trials': trials,
        'candidate_passed_both': passed,
        'original_first_attempt_reclassified': False,
        'automatic_sql_rewriting': False,
        'runtime_limits_changed': False,
        'llm_calls': 0,
        'note': 'Gold used solely for offline evaluation. This is NOT an automatic general-purpose optimizer.',
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Offline controlled recovery for frozen X08')
    ap.add_argument('--run-dir', type=Path, default=RUN_DEFAULT)
    ap.add_argument('--db', type=Path, default=DB_DEFAULT)
    ap.add_argument('--out', type=Path, default=OUT_DEFAULT)
    ap.add_argument('--mode', choices=['recovery', 'semantic', 'all'], default='all')
    args = ap.parse_args(argv)
    try:
        from app.v2_safe_sql import validate_sql
        run_dir = args.run_dir.resolve()
        report = {'kind': 'posthoc_stage2c_trial', 'mode': args.mode,
                  'source_run': str(run_dir), 'llm_calls': 0,
                  'executed_at_utc': datetime.now(timezone.utc).isoformat()}
        if args.mode in ('semantic', 'all'):
            orig_x03 = verified_first_sql(run_dir, 'X03')
            report['x03_risk_review'] = inspect_semantic_risks(orig_x03)
        if args.mode in ('recovery', 'all'):
            report['x08_candidate_trial'] = trial_verified_candidate(run_dir, args.db, validator=validate_sql)
        out = args.out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                          + '_' + uuid4().hex[:6])
        out.mkdir(parents=True, exist_ok=False)
        path = out / 'trial.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({**report, 'report_path': str(path)}, ensure_ascii=False, indent=2))
        if args.mode in ('recovery', 'all') and not report['x08_candidate_trial']['candidate_passed_both']:
            return 2
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Stage2C recovery trial failed closed: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
