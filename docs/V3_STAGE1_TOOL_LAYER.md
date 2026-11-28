# DataPilot V3.1A · 统一工具服务层

> 状态：**第一步（工具层）**。没有新的 `/api/v3/chat`、没有 LangGraph 自动路由、没有 V3 网页或多轮上下文；后续分阶段加入。V2.0.0 标签不动。

## 设计

`app/v3_tool_service.py` 提供 `V3ToolService`，统一三种工具的返回结果：

| 方法 | 复用对象 | 作用 |
|---|---|---|
| `sql_analysis(question)` | `app.v2_runtime.run_v2_query` | **单轮** Text-to-SQL，保留 V2 安全执行器、只读授权、启发式语义风险阻断；不修复 SQL |
| `business_report(report_id)` | `app.sql_tools.run_report` | 仅允许 V1 已存在的固定报表，直接调用 Python 函数，不经本机 HTTP |
| `delivery_risk_prediction(features)` | `app.main.OrderInput` + V1 已加载 HGB | 同 V1 输入校验，要求五项特征；对模型现有 `predict_proba()` 输出未校准评分，不重新训练模型 |

所有方法返回相同最外层结构：

```json
{
  "tool": "sql_analysis",
  "status": "ok",
  "data": {},
  "message": null,
  "error": null,
  "evidence": {}
}
```

`status` 包括 `ok`、`partial`、`clarify`、`unsupported`、`needs_review`、`error`。**只在 `ok` 或 `partial` 状态展示已执行的 SQL 数据**；`needs_review` 不应携带查询结果。所有结果仅反映已执行/校验的操作，不能代替业务语义 Gold 验证。

### 重要安全边界

- V3 工具服务是**Python 库**，不是对外 API。V2 的仅本机网络限制由 `app.v2_api` 实现，**不自动保护 Python 库**。以后加入 `/api/v3/chat` 时，必须单独实施默认关闭与本机/认证访问控制。
- SQL 后端必须由可信服务端提供，不接受用户指定的 `sql_backend` 或 `sql_runner`。`sql_runner` / `report_runner` 注入只供受信任测试使用。
- SQL 始终通过 V2 `run_v2_query` 的原有语义检查和 SQLGlot/SQLite authorizer/资源限制；不要直接用原始 sqlite3 执行模型 SQL。
- 预测输入直接使用 **V1 的 `OrderInput`**，避免实现两套有差异的状态代码/范围校验。现阶段采用函数内惰性导入，后续如需将 V3 API 直接集成 `app.main`，可进一步将输入 Schema 抽取到共享模块。
- 预测必须从现有应用 lifespan 注入 `model` 与 `metadata`；工具层不反序列化模型文件。`risk_score` 是 **uncalibrated_model_score**，不是可靠的真实延期概率。
- 无自动路由、自动修复、未知表授权、上下文继承或公网发布能力。

## 离线运行（无需调用 DeepSeek）

将 ZIP 解压至项目根目录后，在 `feat/v3-multi-tool-agent` 执行：

```powershell
python -m pytest tests/test_v3_tool_service.py -q
python -m pytest tests -q
python -m scripts.v3_stage1_smoke
```

新测试通过假后端、假模型和临时 SQLite 数据库验证路径，不使用线上 DeepSeek，不更改任何 Gold 或基准日志。已有 V1/V2 测试仍必须通过。`scripts.v3_stage1_smoke` 使用你本机现有的可信 Olist 数据库、已训练 HGB 模型和**固定离线 SQL 提议**验证三工具真实服务路径；这不测试 DeepSeek 自然语言生成能力，也不会修改 Gold 或发送 API 请求。

## 下一阶段：V3.1B

待三个适配器及回归通过后，增加 **独立、默认关闭、仅本机访问** 的 `/api/v3/chat` 和 LangGraph 工具调度；使用注入的 V1 模型，确保一个问题只选择有依据的工具。V3 多轮上下文与受控 SQL 修复作为随后阶段单独验证。
