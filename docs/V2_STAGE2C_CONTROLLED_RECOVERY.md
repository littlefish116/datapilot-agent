# DataPilot V2 · Stage 2C-2 控制候选恢复与语义风险提示

## 定位

本补丁**不是通用 SQL 自动优化器，也不是新的模型实验**。它对已经冻结的 Hard8 首次运行进行独立的后验验证：

- **X08：** 确认最初确实因 `query_timeout` 而失败，并校验首次响应、题集、Gold SQL、数据库、合成边界数据库与人工候选 SQL 的 SHA-256。只有全部一致才会调用原先的 `execute_safe_sql()`，在**不提高 5,000,000 VM 步或 3 秒限制**的前提下评测人工过滤下推候选 SQL。结果和两份 Gold 比较仅发生在后验离线报告中。
- **X03：** 通过 `inspect_semantic_risks()` 发出 `DATE_VALIDITY_GAP` 告警。它只能提示需要审查的规则缺口；没有风险标记不代表 SQL 语义正确。
- **JOIN fan-out：** 仅做保守 `POTENTIAL_JOIN_FANOUT` 提醒，可能存在假阳性，绝不能当成是否允许执行的权限判据。

运行不调用 DeepSeek、不修改 V1 Agent、不修改 `app/v2_safe_sql.py` / `app/v2_sql_policy.py`、不覆盖原始实验报告。**业务数据只读**。人工候选 SQL 不会被自动加入 `/api/chat`。

## Windows PowerShell 操作

先确保上一轮新增的 `docs/V2_STAGE2C_DIAGNOSTICS.md` 和 `evaluation/sql_candidates/X08_filter_first.sql` 已经加入 Git；此次补丁依赖这两个文件和已有诊断模块。

```powershell
python -m unittest discover -s tests -p "test_v2_stage2c_recovery.py" -v
python -m evaluation.v2_stage2c_recovery_trial --mode all --run-dir ".\reports\agent_eval_v2_hard\20261127T094633Z_396342"
```

预期：16 项单元测试通过；X03 `DATE_VALIDITY_GAP`；X08 `candidate_passed_both: true`。此时产生新的报告 `reports/agent_eval_v2_stage2c_recovery/<时间标识>/trial.json`，原始 Hard8 `scores.json` 保留不变。如果候选被篡改、数据库字节改变或原始失败代码不符合，则拒绝继续运行（fail-closed）。

## 指标解释及局限

- `candidate_passed_both=true`：**固定的人工候选 SQL** 在该受控试验下经原安全执行器执行，并与固定真实/合成 Gold 的全部 X08 月份、订单数、商品金额均值和支付金额均值相符。
- `automatic_sql_rewriting=false`：这个效果**不能**算作自动修复或重新生成成功。
- `DATE_VALIDITY_GAP`：SQL 可能只把日期非空当成日期有效；建议用更严格的业务指标约束评估，但不自动修改 SQL。
- 对历史记录的后验调试与原始 8 题第一次模型预测的评测不是同一个指标。
- 限于受信任的本地 Olist 数据库；不是多租户生产级安全设施；从 Gold 比对只能证明已测两份数据一致，不能证明任意未来数据上的语义等价。

## 后续开发门槛

等本轮通过后，下一步才是开发面向**可验证 SQL 结构**的规则化优化（或审计型一次修复），并需要新的从未参与规则选择的压力样本进行评测。不要把 X08 的后验候选视为模型自发修复。
