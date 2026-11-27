# DataPilot V2 — Stage 2C-Pre Hard8 语义鲁棒性实验

## 目标与解释边界

这是针对已发现的 SQL 业务风险设计的 **Hard Benchmark / Targeted Stress Test**，不是从未知问题总体随机抽样的独立泛化估计。尤其 X01 的日期解析问题已经在 H06 后验边界审计中暴露；Hard8 旨在检验这种已知风险能否影响新的模型输出。

- 共 **8 道新业务问题 X01–X08**，与旧 H01–H12、D01–D06 不重合；每题有独立 Gold SQL。
- 在首次 live 时，每题只向 DeepSeek 提问 **1 次**，**不自动重试、不提示参考结果**。当时生成的同一条 SQL 分别在本地真实 Olist 数据库、完整虚构的合成边界数据库上执行，比较两种数据下的指标。
- 两套数据库分别评分：`clean_score`、`edge_score`，任一失败则 `combined_verdict=fail`，未知别名为 `review`。Top-K、按月份排序的查询还会额外检查行顺序。
- 只执行**现有的 `app.v2_safe_sql.execute_safe_sql`**；合成压力测试不会给模型额外提示，SQL 禁止绕过原安全执行器。
- 原始 H01–H12 的文件、评分、哈希与结果目录不会被修改；新报告保存在 `reports/agent_eval_v2_hard/`。

## 新题目覆盖范围

| ID | 关键风险 | 参考指标 |
|---|---|---|
| X01 | 非空但 DATE() 无法解析的日期进入延期率分母 | 有效订单数、延期率 |
| X02 | 商品明细 × 支付明细 JOIN 导致金额重复 | 商品总金额、支付总金额 |
| X03 | 空样本 / 零分母 | 0 笔订单、NULL 延期率 |
| X04 | 客户订单身份与客户去重身份不同 | 复购买家、其订单数 |
| X05 | Top3 的占比不可用 Top3 小计做分母 | 支付方式、金额、占比、顺序 |
| X06 | 多卖家州下订单数不是可加统计量 | 卖家州、商品金额、州内去重订单数 |
| X07 | 恰好两条支付明细，不是至少两条 | 订单数、每单支付总额均值 |
| X08 | 双明细表预聚合后按月统计 | 月份、订单数、双均值 |

`evaluation/v2_hard_fixture.py` 生成的数据库包含无效非空日期、NULL 日期、两商品×两支付、同买家对应多个客户 ID、两笔和三笔支付记录，以及缺少 2019-12 有效订单等条件。此库为**完全合成数据**，不包含真实客户记录。

## Windows PowerShell 运行步骤

在项目根目录 `D:\MyDemo\项目\datapilot-clean-check` 且虚拟环境已激活时：

```powershell
Expand-Archive -LiteralPath "D:\MyDemo\DataPilot_V2_Hard8_Patch.zip" -DestinationPath . -Force
python -m unittest discover -s tests -p "test_v2_hard_benchmark.py" -v
python -m evaluation.v2_hard_benchmark --mode validate
python -m evaluation.v2_hard_benchmark --mode gold
```

前三步均 **0 次 LLM 调用**。`gold` 会分别用真实 Olist 与合成数据库执行 X01–X08 的全部 8 条参考 SQL；本地真实数据验收通过后再进入 live。

应先冻结代码、题目和 Gold SQL：

```powershell
git add benchmarks/v2_hard_cases.json benchmarks/hard_gold_sql
git add evaluation/v2_hard_fixture.py evaluation/v2_hard_benchmark.py
git add tests/test_v2_hard_benchmark.py docs/V2_HARD8_SEMANTIC_ROBUSTNESS.md
git diff --cached --check
git commit -m "test: freeze hard8 semantic robustness benchmark"
```

然后**仅执行一次**真实模型测试：

```powershell
python -m evaluation.v2_hard_benchmark --mode live --live
```

这将发起 8 次 DeepSeek 请求，可能计费。模型输出错误、SQL 被安全层拒绝、结果偏差及未知别名**全部记录**，不要即时修改提示词或重新运行覆盖。

## 保存与无费用重放

每次运行都新建独立时间戳目录，包含：

```text
reports/agent_eval_v2_hard/<run_id>/
  run_manifest.json                 # suite / Gold / catalog / 真实DB / 合成DB 的哈希
  response_sha256.json              # 首次8条响应 SHA-256
  responses/X01.json ... X08.json   # 原始模型响应
  edge_responses/X01.json ...       # 同一SQL在合成DB的运行结果
  gold_real/X01.json ...           # 真实数据库参考结果
  gold_edge/X01.json ...           # 合成数据库参考结果
  synthetic_fixture.db             # 完全合成，无真实客户数据
  scores_partial.json
  scores.json
  summary.json
```

再次评分而不调用 DeepSeek：

```powershell
python -m evaluation.v2_hard_benchmark --mode replay --response-dir ".\reports\agent_eval_v2_hard\<run_id>\responses"
```

重放前校验题集、Gold SQL、业务字典、真实 DB 哈希及原始响应 SHA-256；不会自动修改原始 live 结果。

## 评分纪律

1. **先检查单题响应，再看 Gold 差异**。不能把 Gold SQL、Gold 数值、边界数据库的具体内容加入模型上下文。
2. **首次生成正确率与合成鲁棒率分开报**：`clean_auto_pass / 8`、`edge_auto_pass / 8`、`both_auto_pass / 8`。
3. `review` **不是模型错误，也不是模型正确**。可人工确认字段语义后追加后验审计报告，但不得改写首次 `summary.json`。评分器只接受**本题事前注册的字段别名**。
4. SQL 执行成功不等于计算口径正确。特别关注 `clean_pass_edge_fail`，它直接表明同一个 SQL 在真实数据中符合指标，但在所设合成边界条件下未符合指标。
5. 此处合成压力数据库**不是实际业务分布**；不能据此声称模型在生产环境会以相同频率出错。
6. 安全执行器仅适用于当前本地分析原型，不应直接扩展为未经身份认证的公共查询服务。

## 局限及后续

- 8 道定向困难题样本很少，且 X01 对应已发现的日期失效模式，不能作为全面泛化指标或统计显著性结论。
- Gold 查询在真实 Olist 数据上的兼容性必须由用户本地 `--mode gold` 验证，不能用合成测试替代。
- `execute_safe_sql` 可能以时间/VM预算拒绝较复杂查询；应如实计入执行结果，并检查是否有误拒绝。
- 当前安全 SQL AST 校验不是完备 SQL 血缘分析；不能依据它声称完全阻止所有敏感字段变形泄露。
- 下一阶段 Stage 2C 应根据真实失败分布决定优先级：语法/安全拒绝可考虑受限重写，**静默语义错误**更需要数据质量检查、基于口径的自检或测试反馈机制，而非仅依赖执行失败重试。
