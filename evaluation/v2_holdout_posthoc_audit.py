"""Independent, reproducible POST-HOC human-mapping audit of a frozen first run.

Requires exact response/Gold/score file hashes. This does not automatically infer
metric semantics, does not use Gold SQL to tune a model, and never rewrites the
original run. Its purpose is to independently *verify* documented human mappings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import uuid4

from evaluation import v2_holdout_benchmark as holdout

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = ROOT / 'benchmarks/v2_holdout_posthoc_review_v1.json'
DEFAULT_OUT = ROOT / 'reports/agent_eval_v2_posthoc'
SORT_DESC = {'H02': 'delivered_orders', 'H04': 'payment_amount_brl',
             'H07': 'item_value_brl', 'H08': 'delivered_orders'}


def _read_verified(path: Path, sha: str):
    if holdout.sha256(path) != sha:
        raise ValueError(f'证据哈希不一致，请勿修改原始响应: {path}')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _unique_group_map(rows: list, key: str):
    output = {}
    for r in rows:
        value = r.get(key)
        if not isinstance(value, str) or not value or value in output:
            return None
        output[value] = r
    return output


def compare_documented_mapping(case: dict, response: dict, gold: dict, mapping: dict) -> dict:
    """Only review-backed field mappings; no data-dependent mapping inference."""
    if response.get('status') != 'ok' or not response.get('generated_sql'):
        return {'verdict': 'fail', 'reason': 'generation_not_ok'}
    ex = response.get('execution')
    if not isinstance(ex, dict) or ex.get('status') != 'ok' or ex.get('truncated') is not False:
        return {'verdict': 'fail', 'reason': 'execution_not_ok_or_truncated'}
    rows, columns = ex.get('rows'), ex.get('columns')
    golden = gold.get('rows')
    if (not isinstance(rows, list) or not isinstance(columns, list) or
        not isinstance(golden, list) or not isinstance(gold.get('columns'), list) or
        any(not isinstance(r, dict) for r in rows + golden) or
        len(rows) != ex.get('returned_rows') or len(rows) != len(golden) or not rows):
        return {'verdict': 'fail', 'reason': 'invalid_or_incomplete_rows'}
    expected_fields = {f['gold'] for f in case['fields']}
    if set(mapping) != expected_fields or len(set(mapping.values())) != len(mapping):
        return {'verdict': 'fail', 'reason': 'invalid_mapping'}
    if (len(set(columns)) != len(columns) or
        not set(mapping.values()).issubset(columns) or
        not expected_fields.issubset(set(gold['columns']))):
        return {'verdict': 'fail', 'reason': 'missing_or_duplicate_columns'}
    if (any(any(column not in r for column in mapping.values()) for r in rows) or
        any(any(column not in r for column in expected_fields) for r in golden)):
        return {'verdict': 'fail', 'reason': 'missing_row_fields'}
    if case['key']:
        key = case['key']
        actual_grouped = _unique_group_map(rows, mapping[key])
        gold_grouped = _unique_group_map(golden, key)
        if actual_grouped is None or gold_grouped is None or actual_grouped.keys() != gold_grouped.keys():
            return {'verdict': 'fail', 'reason': 'group_keys_mismatch'}
        compared = [(key_value, actual_grouped[key_value], gold_grouped[key_value])
                    for key_value in gold_grouped]
    else:
        if len(rows) != 1:
            return {'verdict': 'fail', 'reason': 'scalar_row_count_mismatch'}
        compared = [('scalar', rows[0], golden[0])]

    differences = []
    for group_value, actual_row, gold_row in compared:
        for f in case['fields']:
            metric = f['gold']
            tolerance = f.get('tolerance', 0)
            if not holdout._eq(actual_row[mapping[metric]], gold_row[metric], tolerance):
                differences.append(f'{group_value}/{metric}: {actual_row[mapping[metric]]!r} != {gold_row[metric]!r}')
    if differences:
        return {'verdict': 'fail', 'reason': 'value_mismatch', 'differences': differences[:20]}

    # Order matters for Top-K questions. Comparison above deliberately ignores
    # row order; this separate check enforces descending ranking of the metric.
    cid = case['id']
    if cid in SORT_DESC:
        column = mapping[SORT_DESC[cid]]
        try:
            values = [row[column] for row in rows]
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                   for v in values) or any(a < b for a, b in zip(values, values[1:])):
                return {'verdict': 'fail', 'reason': 'ranking_order_mismatch'}
        except (ValueError, TypeError):
            return {'verdict': 'fail', 'reason': 'invalid_rank_values'}
    if cid == 'H06':
        months = [row[mapping['purchase_month']] for row in rows]
        if months != sorted(months):
            return {'verdict': 'fail', 'reason': 'month_order_mismatch'}
    return {'verdict': 'pass', 'reason': 'human_mapping_verified_against_all_gold_rows',
            'checked_rows': len(rows), 'field_mapping': mapping}


def audit_run(run_dir: Path, review_path: Path = DEFAULT_REVIEW,
              suite_path: Path = holdout.SUITE) -> dict:
    review = json.loads(review_path.read_text(encoding='utf-8-sig'))
    cases = holdout.load_suite(suite_path)
    if review.get('status') != 'posthoc-not-a-frozen-first-attempt-metric':
        raise ValueError('该审计文件没有标记为事后复核')
    if holdout.sha256(suite_path) != review.get('suite_sha256'):
        raise ValueError('题集哈希变化；禁止用修改后的 Holdout 进行事后审计')
    original = {}
    for name, key in [('run_manifest.json', 'original_manifest_sha256'),
                      ('scores.json', 'original_scores_sha256'),
                      ('summary.json', 'original_summary_sha256')]:
        original[name] = _read_verified(run_dir / name, review[key])
    manifest = original['run_manifest.json']
    if manifest.get('mode') != 'live' or manifest.get('hashes', {}).get('suite_sha256') != review['suite_sha256']:
        raise ValueError('不是匹配的冻结首次真实运行')
    if {c['id'] for c in cases} != set(review['items']):
        raise ValueError('人工审计记录缺少案例或者包含多余案例')
    outcomes = []
    for case in cases:
        cid = case['id']
        rec = review['items'][cid]
        response = _read_verified(run_dir / 'responses' / (cid + '.json'), rec['response_sha256'])
        if rec.get('decision') != 'pass' or not rec.get('rationale'):
            raise ValueError(f'{cid}: 缺少记录的人工裁决或理由')
        if case['category'] == 'sql':
            gold = _read_verified(run_dir / 'gold' / (cid + '.json'), rec['gold_sha256'])
            result = compare_documented_mapping(case, response, gold, rec['field_mapping_gold_to_model'])
        else:
            conforms = (response.get('status') == case['category'] == rec['expected_behavior']
                        and not response.get('generated_sql') and response.get('execution') is None
                        and isinstance(response.get('message'), str) and bool(response['message'].strip()))
            result = {'verdict': 'pass' if conforms else 'fail',
                      'reason': 'documented_human_behavior_review' if conforms else 'behavior_mismatch',
                      'manual_rationale': rec['rationale']}
        outcomes.append({'case_id': cid, 'category': case['category'], **result})
    summary = {
        'audit_kind': 'posthoc_documented_manual_review',
        'source_run': review['source_run'],
        'original_auto_pass': sum(r.get('verdict') == 'pass' for r in original['scores.json'] if r.get('category') == 'sql'),
        'original_auto_review': sum(r.get('verdict') == 'review' for r in original['scores.json'] if r.get('category') == 'sql'),
        'review_verified_sql_pass': sum(r['verdict'] == 'pass' for r in outcomes if r['category'] == 'sql'),
        'review_verified_behavior_pass': sum(r['verdict'] == 'pass' for r in outcomes if r['category'] != 'sql'),
        'all_cases_verified': all(r['verdict'] == 'pass' for r in outcomes),
        'llm_calls': 0,
        'suite_sha256': review['suite_sha256'],
        'notes': '验证既有人工映射和人工行为决策；不是事前自动评分，也不是一次新模型实验。原始 scores.json 和 summary.json 保持不变。',
        'cases': outcomes,
    }
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Verify manually audited, frozen Holdout12 responses')
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--audit-file', type=Path, default=DEFAULT_REVIEW)
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)
    try:
        result = audit_run(args.run_dir, args.audit_file)
        args.out.mkdir(parents=True, exist_ok=True)
        target = args.out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:6] + '.json')
        holdout.json_save(target, result)
        print(json.dumps({**{k:v for k,v in result.items() if k != 'cases'},
                          'audit_report': str(target)}, ensure_ascii=False, indent=2))
        return 0 if result['all_cases_verified'] else 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        p.exit(2, f'审计失败：{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
