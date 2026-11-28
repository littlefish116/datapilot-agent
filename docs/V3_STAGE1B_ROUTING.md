# DataPilot V3.1B · LangGraph 单轮工具选择

该阶段在 **V3 开发分支** 增加独立 `POST /api/v3/chat`，依赖 V3.1A 的 `V3ToolService`。V1 `/api/chat`、V2 `/api/v2/query` 和已发布 `v2.0.0` 标签都保持不变。

## 执行流程

`LangGraph START → route → execute → END`，每轮只有 **一次结构化路由模型调用**，选择 `sql_analysis` / `business_report` / `delivery_risk_prediction` 中最多一个工具，或返回 `clarify`、`unsupported`。没有循环反思、多轮状态、SQL 自动修复或第二次自由文本总结。

- `sql_analysis`：原始问题直接交给 V3.1A → V2 `run_v2_query`，仍经 SQLGlot / SQLite 只读授权与启发式语义风险拦截；**SQL 生成会再调用一次 DeepSeek**，因此动态 SQL 问题通常消耗两次 LLM 请求。
- `business_report`：直接用现有固定报表执行器，不经过本机 HTTP；模型只负责选择报表 ID。
- `delivery_risk_prediction`：路由器提取明确出现的五项特征，V3.1A 再使用 V1 `OrderInput` 校验和预加载 HGB 模型推理；缺失特征时追问，评分未经概率校准。路由模型可能提取错误特征，因此**人工验收需要检查 tool_result.evidence.input_features**。

响应包含 `status`、`selected_tool`、`used_tools`、`answer`、`tool_result`、`route_usage`、`automatic_repair:false`。`answer` 由程序根据工具返回的**实际数据**生成，不调用另一个大模型臆测业务含义。SQL 成功不代表业务语义 Gold 验证通过；`partial` 不可当作完整数据；`needs_review` 不呈现执行结果。

### 安全与部署

- 环境变量 `DATAPILOT_V3_ENABLED=1` 才开启；API 校验 ASGI 客户端地址仅为 `127.0.0.1` 或 `::1`，不要用 `--host 0.0.0.0`，**没有身份验证、频率/费用限制，不可公开部署**。
- `V3ToolService` 的依赖注入只在受信任服务端执行；模型和业务 SQLite 均沿用本机原有路径。所有错误对用户采用简短安全消息。
- 路由 JSON 必须严格符合协议，不允许用户输入 SQL 取代工具内的安全检查，不允许指定任意报表或预测字段。
- 该版本没有会话 ID 和跨轮继承；下一阶段 V3.2 单独引入状态管理。

## 安装及测试（Windows PowerShell）

在项目根目录、`feat/v3-multi-tool-agent` 分支解压 ZIP，然后：

```powershell
python scripts/install_v3_api.py
python -m pytest tests/test_v3_routing.py tests/test_v3_api.py -q
python -m pytest tests -q
python -m scripts.v3_stage1b_smoke
```

测试和 `smoke` 不联网、不调用 DeepSeek，也不修改现有数据和模型。`smoke` 在已有的可信本机 Olist 数据和保存的 HGB 模型上，用**固定模拟路由/模拟 SQL 后端**测试三条真实工具链。可不启动 Uvicorn。

### 可选真实联调（会产生模型费用）

```powershell
$env:DATAPILOT_V3_ENABLED = "1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在**另一个** PowerShell 窗口发送 UTF-8 请求：

```powershell
$body = @{question="查询每个月的订单延期率"} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v3/chat" -Method POST -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 12
```

成功时 `selected_tool` 应为 `business_report`，`used_tools` 只有一个条目，且 `tool_result` 有实际结果。注意：模型路由并非确定性规则，真实准确率需要后续专门的路由评测；若选错工具，应如实记录，而不是将结果改成成功。
