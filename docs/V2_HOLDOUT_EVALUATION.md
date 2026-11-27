# DataPilot V2 · Stage 2B-Holdout：首次生成独立评测

## 实验目的与性质

本轮是 **12 道同分布未见题的单次首次生成实验**，不是训练集性能、不是生产级准确率、也不是跨数据集泛化测试。为避免此前 D01–D06 开发题的反复调试带来过度拟合，H01–H12 在真实调用前固定问题、参考 SQL、比较字段、允许别名和数值容限。**先执行 `validate` 与 `gold`，确认参考口径和执行成功，再统一运行第一次 `live`；不要在首次完整评测前依据新题修改提示词。**

题目构成：

| 类型 | 题号 | 数量 | 验收重点 |
|---|---|---:|---|
| 可以回答的 SQL | H01–H08 | 8 | 新月份、Top-K、多表 JOIN、订单粒度、支付粒度、延期率、英文分类、去重买家 |
| 应澄清 | H09–H10 | 2 | 未明确“销量”指标或“最近”时段 |
| 应拒绝/不支持 | H11–H12 | 2 | 未开放评论字段、未开放地理地址/经纬度明细 |

模型 **只看到用户问题和 Stage 2B 原有 Schema Context**。冻结题集文件中有参考 SQL 路径，但不作为模型提示词的一部分；参考 SQL 只在评分阶段由本地只读 SQLite 执行。

## Windows PowerShell 操作

在项目根目录及 `.venv` 下执行：

```powershell
# 1. 不联网、不读真实数据库：检查题集、Gold 文件和固定哈希
python -m evaluation.v2_holdout_benchmark --mode validate

# 2. 只读本地 Olist：验证 8 条参考 SQL 能计算出非空、未截断结果；无 LLM 调用
python -m evaluation.v2_holdout_benchmark --mode gold

# 3. 在 Gold 全部成功之后，执行首次真实 DeepSeek 调用，共 12 道 / 每题 1 次（可能收费）
python -m evaluation.v2_holdout_benchmark --mode live --live
```

`live` **没有自动重试**。如果本次出现错误，保留失败记录，不要先改提示词再重新运行并把结果算作“首次生成”。

每次运行都会新建 `reports/agent_eval_v2_holdout/<UTC时间>_<短ID>/`，包含 `run_manifest.json`（题集及 Gold SQL 的 SHA-256、数据库文件大小和 mtime）、`responses/H01.json` … `H12.json`（原始响应）、`gold/H01.json` … `H08.json`、`scores.json` 和 `summary.json`。进程异常退出时，已完成的题目原始响应仍会被保存，但整轮可能缺少最终汇总；须如实记录。

若需离线重复评分（**不调用 API**）：

```powershell
python -m evaluation.v2_holdout_benchmark --mode replay --response-dir ".\reports\agent_eval_v2_holdout\<首次运行目录>\responses"
```

实际操作时，把 `<首次运行目录>` 替换为终端输出的真实目录名。

## 评分规则与局限

- **SQL 题**：要求生成 SQL、经安全执行成功、结果未截断、所有分组键完整、所有请求指标与 Gold SQL 一致；数值容限在 `benchmarks/v2_holdout_cases.json` 中预先定义。
- **列名问题**：允许预先登记的别名。遇到新别名，判为 `review` 而不是自动判错或追加别名；需对首次原始响应人工复核。
- **非 SQL 题**：自动检测 `clarify` / `unsupported` 决策、非空说明、未执行 SQL；只能判定**行为格式匹配**。说明理由是否与问题匹配，仍需人工复核。
- `summary.json` 中的 `sql_first_attempt_accuracy_strict` 只统计 8 道 SQL 题中的自动严格通过率；`behavior_format_match_rate` 单独统计 4 道澄清/拒绝题，二者不混为一个数字。
- 如果 SQL 有缺失分组、金额偏差、错误执行或超时，均记录为首次生成失败；没有“先修好再算第一次”。
- Gold SQL 独立人工编写且经过合成数据库的 SQL 语法测试，但只有在你的本地完整 Olist 数据库执行 `--mode gold` 成功后，才能视为该真实数据上的参考可用。
- H01–H12 仍共享原始 Olist 数据和同一批指标定义，不能声称跨业务场景的泛化效果。样本只有 12 道，统计不确定性很大。
- `run_manifest.json` 为数据库保存大小和修改时间，而非数据库文件 SHA-256；如需严格复现，须额外保证同一版本数据文件。
- 不要把本地 `.env`、密钥、数据库文件、运行输出提交到公共仓库。评测源码、固定题集及 Gold SQL 可以提交。

## 下一步

对 `summary.json`、`scores.json`、H09–H12 的原始 `message` 做人工复核。将失败或 `review` 题型分类为：Schema 不理解、统计粒度错误、非法字段、SQL 语法/执行失败、超时、列名评分映射，以及澄清/拒绝理由不充分。之后再决定是否有证据支持加入 Stage 2C 有限次错误恢复。
