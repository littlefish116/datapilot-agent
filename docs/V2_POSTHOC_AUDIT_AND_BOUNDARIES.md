# DataPilot V2 — 后验语义核验与边界数据测试

## 目标与实验纪律

本补丁新增两条**离线**验证流水线，不修改 `app/`、既有 V1/V2 Agent、冻结版 `benchmarks/v2_holdout_cases.json` 或 H01–H08 Gold SQL；不调用 DeepSeek。

1. **后验审计**：基于 `benchmarks/v2_holdout_posthoc_review_v1.json` 内已经人工核对过的字段映射，以及原始运行文件的 SHA-256，自动重复逐行数值校验（包括分组去重、数值容差、Top-K 排序）。对未知字段**不做未经审核的自动语义推断**。这是验证人工裁决的可重复性，并非重新计算未经人工参与的首次自动准确率。
2. **边界测试**：构建临时 SQLite 数据，检查 H06 首次生成 SQL 与 Gold SQL 在不合法日期字段上的差异；另示范一对多 JOIN 的 fanout 及零分母处理。这些是新增的压力场景，不计入原始 Holdout 12 题结果。

保持原始 `scores.json` 中的 **4 pass / 4 review**；后验裁决为 **8/8**。这两个数字分别描述不同的评价程序，不能相互替代。

## Windows PowerShell 操作（在项目根目录）

```powershell
Expand-Archive -LiteralPath 'D:\MyDemo\DataPilot_V2_Posthoc_Audit_Boundaries_Patch.zip' -DestinationPath . -Force
python -m unittest discover -s tests -p 'test_v2_posthoc_audit.py' -v

python -m evaluation.v2_holdout_posthoc_audit --run-dir '.\reports\agent_eval_v2_holdout\20261127T092513Z_269a98'
python -m evaluation.v2_boundary_probe --run-dir '.\reports\agent_eval_v2_holdout\20261127T092513Z_269a98'
```

**预期**：新增 14 项离线单元测试通过；审计显示 `review_verified_sql_pass: 8`、`review_verified_behavior_pass: 4`、`llm_calls: 0`；边界探针中 `H06_malformed_nonnull_dates.divergence: true`，Gold 订单数 2、延期率 50%，首次模型 SQL 订单数 4、延期率 25%。

报告分别生成于：

- `reports/agent_eval_v2_posthoc/` （新审计报告，不覆盖首次响应）
- `reports/agent_eval_v2_boundaries/` （合成压力测试结果）

如果原始首次运行目录不是上面的路径，使用正确的 `--run-dir` 参数即可；目录必须包含 `run_manifest.json`、`summary.json`、`scores.json`、`responses/H01.json`–`H12.json` 和 `gold/H01.json`–`H08.json`。

## H06：非空不等于日期有效

冻结版模型 SQL 的筛选条件：

```sql
order_delivered_customer_date IS NOT NULL
AND order_estimated_delivery_date IS NOT NULL
```

Gold SQL 使用：

```sql
DATE(order_delivered_customer_date) IS NOT NULL
AND DATE(order_estimated_delivery_date) IS NOT NULL
```

例如字符串 `not-a-date` 非空，但 `DATE('not-a-date')` 是 NULL，因此前者可能把错误日期纳入分母。合成记录包括：有效按时、有效延期、实际日期格式错误、预计日期格式错误、日期为 NULL。此处 Gold 只接受前两条（延期率 50%）；首次模型 SQL 把两条非空的错误日期纳入分母（延期率 25%）。

该差异**不影响**在原始 Olist 数据上的 8/8 审计结果，但证明这些 SQL 在扩展数据分布下不完全等价。未来可以在独立困难题集里专门评估这类问题。

## JOIN fanout

一笔订单有两条商品（价格分别 10、20）和两条支付：商品明细直接 JOIN 支付明细形成四行，直接 `SUM(price)` 得到 60，而商品金额真实值是 30。正确方法是在订单/商品粒度先聚合，或不联结不必要的支付表。

## 零分母

将比例的分母写成 `NULLIF(COUNT(*), 0)`，在没有符合条件的记录时返回 SQL NULL，而不是制造非有限值或假装存在数据。NULL 如何对外呈现，需要上层答复机制明确说明“没有可计算的样本”。

## 版本控制建议

只提交以下正式文件：

```powershell
git add evaluation/v2_holdout_posthoc_audit.py evaluation/v2_boundary_probe.py
git add benchmarks/v2_holdout_posthoc_review_v1.json tests/test_v2_posthoc_audit.py
git add docs/V2_POSTHOC_AUDIT_AND_BOUNDARIES.md
git diff --cached --check
git commit -m 'test: audit holdout aliases and probe SQL edge cases'
```

不要把 `.env`、本地 Olist 数据库、`reports/`、原始响应 ZIP 或用户目录加入公开仓库。

## 局限

- 审计映射是**首次调用之后**经人工确认的，不可作为“模型无需人工即可理解任意别名”的证据。后续题集可事前设计基于 SQL 语义、字段类型和业务元数据的映射策略；无法唯一识别时继续保持 review。
- 评测仍是 12 道小样本，结果不能估计生产环境总体准确率。
- 合成探针确认的是这次 H06 SQL 在特定异常数据下的缺陷，不意味着所有日期格式或数据库引擎都具有相同表现。
- H11/H12 的拒绝措辞仍应由后续答复层调整为“当前授权查询范围没有开放这些字段”，而非说数据库物理上不存在相关表。
