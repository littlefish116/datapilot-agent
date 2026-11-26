# DataPilot Agent V1 — 可复现基线评测（第一阶段）

本目录为 **V2 改进之前**建立的 V1 基线。只增加评测能力，**不修改现有 Agent、SQL 报表和模型**。

## 实际覆盖范围

- `benchmarks/v1_cases.json`：24 个评测场景、27 轮提问。
- `benchmarks/gold_sql/*.sql`：6 个动态问题的**人工编写**参考 SQL；已有固定报表复用 `sql/queries/*.sql`。
- `benchmarks/metric_catalog.json`：核心指标口径、表关系、JOIN 粒度与常见错误。
- `evaluation/v1_runner.py`：离线题库校验、只读参考 SQL 查询、调用真实 V1 API 的可选评测。
- `tests/test_agent_benchmark.py`：不依赖原始数据、模型或外部 LLM 的离线单元测试。

> 注意：**V1 不支持 Text-to-SQL**。金标准 SQL 只会在单独的本地 SQLite 连接里执行，用于人工判断真实数据是否正确；它们绝不会作为模型生成的 SQL 传入 V1 执行。

## 1. 离线验证：无需原始 CSV、模型、API Key

把本代码包中的文件合并到 DataPilot 仓库根目录后，执行：

```powershell
python -m evaluation.v1_runner --mode validate
python -m unittest discover -s tests -p "test_agent_benchmark.py" -v
```

预期：识别 **24 cases / 27 turns**，并且 **7 项离线基准单元测试通过**。这只证明题库结构和评测程序行为正常，不证明 Agent 回答正确。

## 2. 生成标准参考结果：需要本地 Olist 数据库，但不调用 LLM

先按主 README 重建 `data/processed/olist.db`，然后运行：

```powershell
python -m evaluation.v1_runner --mode references
```

参考结果写入 `reports/agent_eval_v1/<UTC时间>_<短ID>/references.json`。

人工审核参考 SQL 时请重点关注：订单粒度 vs. 商品条目粒度、`orders × items × payments` 多重 JOIN、是否筛选 `delivered`、延期日期定义，以及买家身份键 `customer_unique_id`。建议先抽查并确认这 6 条新参考 SQL 的业务含义，再把它们用于正式对照结论。即使 SQL 能执行，也不保证业务口径完全正确。

## 3. 真实 V1 测试：**会产生 DeepSeek API 调用和费用**

先在本地完成数据/模型重建，准备好 `.env`，启动另一个 PowerShell 终端：

```powershell
python -m uvicorn app.main:app --reload
```

在项目根目录的测试终端先运行 3 个单场景小样本：

```powershell
python -m evaluation.v1_runner --mode live --max-cases 3
```

确认运行稳定后，再按需求扩大（可先只跑 F01 和 P01）：

```powershell
python -m evaluation.v1_runner --mode live --case-id F01 --case-id P01
python -m evaluation.v1_runner --mode live
```

各场景使用不同的 `conversation_id`，同一场景中的后续追问沿用 ID。请只在**确定拥有 API 配额**时使用 `--mode live`。

每次真实评测会写入本地忽略目录 `reports/agent_eval_v1/...`：

- `results.jsonl`：原始问题、回答、工具名称、耗时、参考 SQL 结果及错误记录。
- `summary.json`：API 成功率与**可自动核验样例**的工具选择匹配率。
- `manual_review.csv`：留给人工补填的答案正确性/行为正确性与错误分析记录。

`reports/` 已由项目 `.gitignore` 排除，不要把真实对话、带隐私的信息或凭证上传到 GitHub。

## 4. 评分规范：不要混淆不同指标

| 指标 | 当前能否自动算 | 含义与限制 |
|---|---|---|
| HTTP 调用成功率 | 能 | 仅证明 API 有正常响应，不等于答案正确 |
| 工具选择匹配率 | 部分能 | 只对 `score_tool_selection=true` 的题目检查 `used_tools`；不含工具参数 |
| 参考 SQL 返回结果 | 能（依赖本地 DB） | 可用于人工核对，不代表 Agent 执行了参考 SQL |
| 自然语言答案正确率 | **不能，需人工** | 对照标准结果、筛选口径和是否有错误因果解释 |
| 动态 SQL 覆盖率 | V1 不适用 | V1 没有 SQL 生成工具；V2 实现后才可评估 |
| 拒绝/澄清正确率 | **不能，需人工** | 检查危险操作、模糊问题、错误的业务口径 |
| 延迟 | 能 | API 单轮壁钟耗时；受网络/服务波动影响 |
| Token 成本 | **不能** | 当前 `/api/chat` 不返回 Token usage，不能凭响应长度猜测 |

人工评分建议每轮分别填：

- `answer_correct_0_1`：`1` = 数值和业务口径均正确；`0` = 数值错误、回答编造或漏掉关键口径；不适用则保持空白。
- `behavior_correct_0_1`：`1` = 合理回答、澄清、拒绝或谨慎说明限制；`0` = 错误操作或无依据地给出确定结论。
- `notes`：记录失败类型，例如 `wrong_join_grain`、`hallucinated_metric`、`missed_context`、`unnecessary_refusal`、`incorrect_tool_choice`。

其中动态查询场景的 `expected_behavior=unsupported` 是**V1 的能力边界标记**，不是说拒绝回答一定优于 V2 正确完成查询。应在对照报告中分别统计 V1 能力覆盖率和共同支持问题上的答案正确率，不能用所有题目混成一个总分。

## 5. V1/V2 公平对照的实验协议

1. **冻结 V1 代码版本**（记录 Git commit SHA 或新建本地标签），不要在做 V2 时直接覆盖 V1。
2. 提前冻结题库与标准答案。扩展题库后更新版本号，不要根据 V2 的输出修改预期结果。
3. 使用一致的 Olist 数据快照、DeepSeek 模型标识和模型参数；每个版本独立运行相同的题目。
4. `F/P/M` 是共同任务；`D` 是 V2 的新增动态 SQL 能力；`A/S` 衡量澄清和安全策略。
5. 每个版本报告支持范围、正确率、拒绝率、延迟与失败案例。不要把 `used_tools` 的名称匹配直接写成“Agent 正确率”。
6. 正式 V2 实验可以对非确定性 LLM 调用做多轮重复并记录波动；V1 第一次先做小样本排查。

## 6. 与下一阶段的边界

只有在这套 V1 评测完成、参考 SQL 审核通过后，再开发 **受控 Text-to-SQL**：schema/业务口径检索 → 生成 SQL → AST 校验 → 只读执行 → 结果核验 → 有限错误恢复。当前不引入 StateGraph 重构、Docker、Redis 或新的向量数据库。
