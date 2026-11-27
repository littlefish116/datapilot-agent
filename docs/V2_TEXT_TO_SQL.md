# DataPilot V2 · Stage 2B：独立 Text-to-SQL 流水线

本阶段在 Stage 2A 安全 SQL 执行器已经通过 11 项单元测试以及 6 条 Gold SQL 冒烟检查的基础上进行。
**不会修改** `app/main.py`、`app/agent_runtime.py`、`app/agent_tools.py` 或现有 V1 Benchmark；也不会添加新 API 路由。

## 新增文件

- `app/v2_schema_context.py`：从 `app/v2_sql_policy.py` 的强制白名单生成真实允许访问的表、列清单；读取 `benchmarks/metric_catalog.json` 的业务指标与 JOIN 口径，排除与未授权表有关的关系。
- `app/v2_text_to_sql.py`：给 DeepSeek 提供 Schema 上下文，只接收 JSON 形式的 `sql` / `clarify` / `unsupported` 决策，单次调用，不自动重试；合法 SQL 始终调用 Stage 2A 的 `execute_safe_sql()`。
- `evaluation/v2_text_to_sql_smoke.py`：CLI 演示及逐题实验入口，必须显式指定 `--live` 才联网调用模型。
- `tests/test_v2_text_to_sql.py`：14 项离线测试，不需要真实 API Key。

## Windows PowerShell：本地集成步骤

在 `D:\MyDemo\项目\datapilot-clean-check`、`feat/v2-text-to-sql` 分支、已激活 `.venv` 的前提下：

```powershell
# 先检查当前状态，不要在工作区已有改动时直接覆盖文件
 git status
# 在项目根目录解压新补丁（用你实际下载路径替换）
Expand-Archive -LiteralPath "D:\MyDemo\DataPilot_V2_Stage2B_Patch.zip" -DestinationPath . -Force

# 无须新增 pip 包：沿用已安装的 Stage 2A SQLGlot、V1 openai 和 python-dotenv
python -m unittest discover -s tests -p "test_v2_text_to_sql.py" -v
python -m unittest discover -s tests -p "test_v2_safe_sql.py" -v

# 只打印允许访问的 schema 和指标字典；不会消耗 DeepSeek 额度
python -m evaluation.v2_text_to_sql_smoke --show-context
```

## 第一次真实 Text-to-SQL

确保项目 `.env` 包含可用 `OPENAI_API_KEY`、`OPENAI_MODEL=deepseek-chat`、`OPENAI_BASE_URL=https://api.deepseek.com`，且本地 `data/processed/olist.db` 存在：

```powershell
python -m evaluation.v2_text_to_sql_smoke --case-id D01 --live
```

这条命令每次执行通常发起一次真实模型请求，可能产生 API 费用。**无须启动 Uvicorn**：数据直接交给本地只读 SQL 执行器。

执行成功示例结构（数值不是承诺，也不是预填答案）：

```json
{
  "status": "ok",
  "question": "...",
  "generated_sql": "SELECT ...",
  "message": null,
  "generation_ms": 1250.0,
  "usage": {"prompt_tokens": 1000, "completion_tokens": 150, "total_tokens": 1150},
  "execution": {"status": "ok", "rows": [{"payment_type": "credit_card", "amount_brl": 1.0}]},
  "error": null
}
```

如果模型返回了不符合协议的 JSON、生成了被拒绝的 SQL、网络不可用或者数据库缺失，则会显示结构化错误。**没有自动修复，也不会悄悄重复请求**。

D01 运行后再按 D02、D03 …… D06 逐项测试并保存真实结果，不要提前认为 SQL 生成准确。对照之前的 `references.json`，需要逐行对比全部结果，而非仅检查第一行。

## 本阶段边界

- Schema Prompt 仅供指导，并不授予权限；真正的权限是 Stage 2A 的 AST + `set_authorizer()` + SQLite `mode=ro`。
- `status=ok` 只代表 SQL 执行完成；它可能有 JOIN Fan-out、时间口径或分母错误，必须用 Gold SQL 结果核验。
- 目前只有 **单轮自然语言查询 → 结构化 SQL 执行结果**，尚不支持独立的回答生成、多轮上下文、错误自修复，也没有接入 FastAPI。
- 本阶段只用于本地可信开发和评测。不能直接公开部署或视为防数据泄漏机制；底层允许内部 JOIN 键，存在间接信息泄漏/推断风险。不得让不受信任的用户直接发送任意查询。
- 如果不需要人工手动运行真实 API，就不要使用 `--live`。
