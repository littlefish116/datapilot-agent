# DataPilot V2 — Stage 2B-Eval（已知开发题的自动评分）

本补丁只添加评测代码：**不改 V1 Agent、V2 SQL 生成器、SQL 安全执行器或业务数据库**。

## 1. 安全的离线检查

```powershell
python -m unittest discover -s tests -p "test_v2_result_evaluator.py" -v
python -m evaluation.v2_benchmark --mode validate
```

`validate` 不调用 LLM，也不访问数据库，只核对 D01–D06 与参考 SQL 文件。代码依赖已有 `benchmarks/v1_cases.json`、`benchmarks/gold_sql/*.sql` 与 `evaluation/v1_runner.py`。

## 2. 可选：真实评测（6 次模型调用，可能计费）

```powershell
python -m evaluation.v2_benchmark --mode live --live
```

如只测试两道：

```powershell
python -m evaluation.v2_benchmark --mode live --live --case-id D02 --case-id D03
```

运行时每题仅发起一次 DeepSeek 请求；无自动重试。如果 Gold SQL 不能在你的只读 Olist 数据库成功运行，则不把该题判为模型失败。输出保存到 `reports/agent_eval_v2/<UTC时间戳>_<随机后缀>/`，包括 `responses/Dxx.json`、`scores.json`、`summary.json`。

## 3. 不用额外 Token 复评已经保存的结果

```powershell
python -m evaluation.v2_benchmark --mode replay --response-dir ".\reports\agent_eval_v2\<上次目录>\responses"
```

`replay` 默认重新执行本地 Gold SQL，并对已有 JSON 结果进行比较，不调用 LLM；传 `--reference-snapshot` 时改为使用已保存的参考快照。请把尖括号占位符替换为实际输出的目录。

## 4. 判定规则与局限

- **准确度**：按 `payment_type` / `customer_state` / `seller_state` 等分组键进行无序比较；单值案例逐项比较。
- **数值容差**：金额和百分率采用 `0.011` 绝对容差；D03 平均商品条目数采用 `0.0001`。订单数是严格整数相等。
- **必须**：安全执行成功、实际返回完整行、分组全集一致、所有**要求回答的**指标字段可比且一致。
- **不打分的 Gold 辅助列**：D01 `orders_using_type`、D02/D03 `delivered_orders`、D05 `late_orders`、D06 `delivered_orders_with_seller_state`。这些并非各题自然语言提问要求。评测不能声称全字段吻合。
- **限制**：固定字段映射基于本轮已经通过的模型输出别名；若未来生成同义别名，可能被判为 `fail`，需人工复核，不能在看过测评答案后偷偷调整评分规则。
- **注意**：D01–D06 已被反复用于调试，属于已见开发集，6/6 不是未见样本准确率。建议下一阶段冻结独立 holdout 题库，不能用其失败案例反复调提示词并继续称它为盲测集。
- **不支持**：该脚本不评价 SQL 安全策略是否足够安全，也不检验自然语言回答表达是否恰当。

## 5. Git

```powershell
git add evaluation/v2_result_evaluator.py evaluation/v2_benchmark.py tests/test_v2_result_evaluator.py docs/V2_EVALUATION.md
git diff --cached --check
git commit -m "test: add reproducible v2 text-to-sql evaluation"
```

`.env`、本地数据库、整个 `reports/` 评测数据不要直接公开推送。先检查 `.gitignore` 和 `git status --short`。Windows 默认编码的 `Get-Content` 若有乱码，请加 `-Encoding UTF8`。

## 6. 随补丁附带的历史结果重放（**0 次模型调用**）

补丁中的 `v2_replay_first_run/D01.json` 至 `D06.json` 是按你此前在聊天中粘贴的六次终端 JSON 输出整理的 **历史结果副本**。这是用户提供的聚合结果，不是新调用、新测试集或者模型再次作答。D01 已按当时粘贴的最终可见字段重建；如果你有原始日志文件，请以原始日志为准。

若你保留了 V1 的 `references.json` 文件，可直接离线对照，无需安装 Olist 数据库或调用 DeepSeek：

```powershell
python -m evaluation.v2_benchmark --mode replay --response-dir .\v2_replay_first_run --reference-snapshot "<你实际保存的 references.json 路径>"
```

也可以去掉 `--reference-snapshot`，脚本将使用 `data/processed/olist.db` 重新执行 Gold SQL。

**提醒**：`v2_replay_first_run/` 是历史评测临时数据。正式 Git 提交仅暂存 4 个源代码与文档文件；不要把其中的用户日志当成新的独立测试数据。
