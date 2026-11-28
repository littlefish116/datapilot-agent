# DataPilot V3.3 · 受控 SQL 一次修复（实验版）

基线：`feat/v3-multi-tool-agent` 分支提交 `ff39be4`。V2.0.0 标签、V1/V2 API、`app/v2_safe_sql.py`、`app/v2_sql_policy.py`、已有 Holdout/Hard8 Gold 与原始实验成绩全部保持不变。

## 功能边界

V3 `sql_analysis` 默认**不启动**修复。只有服务器运行时显式设置 `DATAPILOT_V3_SQL_REPAIR_ENABLED=1`，并且 `DATAPILOT_V3_ENABLED=1`，才允许：

1. 按 V2 `run_v2_query()` 执行首次 Text-to-SQL。正常结果直接返回，**不消耗额外 LLM 调用**。
2. 精确诊断首次失败：仅限原始 SELECT 的明确 `syntax_error`（通过同一 V2 SQLGlot 验证器核实），或 V2 安全执行器真实返回的 `query_timeout` 且此前无语义风险。其他错误、`clarify`、`unsupported`、`needs_review` 一律不尝试。
3. 最多向同一个可信 SQL 后端提出 **1 次**受限修复请求，严格解析 `{status:sql,sql:...}`。不会从 Gold、人工 X08 候选、报告中寻找答案。此步骤可能产生额外 DeepSeek Token 费用。
4. 候选先经过 `validate_sql`。对于预算失败，还进行保守的物理表集合、SQL 常量、最终输出别名检查；改变者拒绝。该检查**不是语义等价证明**。
5. 候选经 `run_v2_query` 重新走完整的语义风险检查、SQLGlot 白名单、SQLite 只读 authorizer、结果体积与时长/VM 限额。**默认仍是 3 秒、5,000,000 VM 步**，不允许用宽松预算或绕过执行器来换取成功。
6. 响应的 `tool_result.evidence.recovery` 记录首次错误、原 SQL、候选、结果、额外模型用量以及 `semantic_equivalence_proven:false`。`automatic_repair=true` **仅在候选真正通过完整流水线、返回 ok/partial 时出现**；被拒绝、语义复核或再次失败都不是成功。

安全提醒：即使候选通过了这些检查，也**不能证明它与原始问题在全部数据上具有相同业务语义**。特别是对于费用、支付金额、复杂 JOIN 与日期口径，不应因为一次技术执行成功就声称结果经 Gold 验证。仍然只适用于可信本机离线原型，不能暴露公网。

## 适用与禁止修复的典型情况

| 首次状态/错误 | 是否提出修复 | 原因 |
|---|---|---|
| 正常 SQL `ok`、`partial` | 否 | 不重复查询 |
| `query_timeout`（安全执行器确认，且无风险） | 可以一次 | 限流条件下的 SQL 结构优化 |
| SQLGlot 明确的 SELECT `syntax_error` | 可以一次 | 重新生成规范 SELECT |
| `unsafe_sql` / `forbidden_table` / `forbidden_column` | 否 | 不允许自动扩大授权 |
| `needs_review` / 日期有效性风险 / JOIN fan-out | 否 | 必须人工复核 |
| `execution_error` / `database_error` / `llm_api_error` | 否 | 无可靠自动修复依据 |
| 修复候选再次超时或被拒绝 | 不再尝试 | 一次预算已耗尽 |

## 安装及本地验证（Windows PowerShell）

在项目根目录、`feat/v3-multi-tool-agent` 分支（`ff39be4` 或后续兼容提交）运行：

```powershell
Expand-Archive -LiteralPath "D:\MyDemo\DataPilot_V3_Stage3_Controlled_SQL_Recovery_Patch.zip" -DestinationPath . -Force
python scripts/install_v3_sql_recovery.py
python -m pytest tests/test_v3_sql_recovery.py tests/test_v3_sql_recovery_wiring.py -q
python -m scripts.v3_stage3_smoke
python -m pytest tests -q
```

冒烟测试使用真实 Olist 数据库和原 V2 安全流水线、用模拟 DeepSeek 决策；**首个 query_timeout 是合成的，不代表 X08 首次真实执行已自动修复**。它检验候选实际重新经过 V2 安全执行，并返回 delivered 96,478；同时检验非法评论表没有机会重试。验证结束后还需要另做真实 DeepSeek 请求、异常路由与新压力题的独立评估。

真实请求（会消耗 API 费用）：

```powershell
$env:DATAPILOT_V3_ENABLED="1"
$env:DATAPILOT_V3_SQL_REPAIR_ENABLED="1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在另一 PowerShell 窗口通过 POST `/api/v3/chat` 提问。只有原始 SQL 失败且满足严格条件，才会再次调用模型。正常提问不会为验证修复而故意执行超预算 SQL。检查 `automatic_repair` 和 `tool_result.evidence.recovery`，确认它们没有将一次成功执行误当作语义正确证明。

## Git 提交范围

使用 `git add` 明确暂存：`app/v3_sql_recovery.py`、`app/v3_tool_service.py`、`app/v3_api.py`、`app/v3_routing.py`、`scripts/install_v3_sql_recovery.py`、`scripts/v3_stage3_smoke.py`、`tests/test_v3_sql_recovery.py`、`tests/test_v3_sql_recovery_wiring.py`、`docs/V3_STAGE3_SQL_RECOVERY.md`。不要提交 `.bak`、ZIP、`.env`、实验原始日志或数据库。只有实机测试通过才提交。

后续应在新的、未用于开发规则的压力题上分别统计首次成功率、修复资格率、候选通过率、语义 Gold 一致率、额外 Token、耗时与安全拒绝率。**不得把 V2 人工 X08 候选的成功计入 V3 自动修复成绩。**
