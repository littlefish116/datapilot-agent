# DataPilot V3.2 · 单进程多轮上下文（候选）

> **开发分支**：`feat/v3-multi-tool-agent`，要求已包含 V3.1B 提交 `139a69a`。
> V2.0.0 标签与 V1/V2 HTTP 接口保持不变。当前仍为本机试验版，不面向公网。

## 交付范围

- 修改 `app/v3_api.py`：`POST /api/v3/chat` 在原有 `question` 上新增可选 `conversation_id`（UUID）和 `reset_context`（布尔）。不携带 ID 的请求创建会话，响应返回新的 ID；携带同一 ID 的请求可承接先前已确认的问题。响应增加 `effective_question`（本轮真正交给 LangGraph 的独立问题）、`context_used`、`context_resolution`、`session_turn`。
- 新增 `app/v3_conversation.py`：在原有 **V3.1B LangGraph 单轮决策/执行**外部提供明确的、容量受限的单机内存会话状态。此处**没有使用 LangGraph checkpointer，也不声称跨进程持久化**。
- 会话记录只保留上一轮通过的独立问题、所用工具和最少状态，不保存 SQL 结果表、SQL 文本、原始模型返回或对话全文。默认 45 分钟无活动过期，最多 128 个会话，每个会话连续 12 轮后主动清空旧上下文；同一会话调用串行化。
- “查询2018年3月的订单延期率”→“那4月呢？”可确定性继承原指标和年份，将后者改写为“查询2018年4月的订单延期率”，之后**重新调用** V3.1B 路由与 V2 SQL 安全管线。
- “查询每个月的订单延期率”→“那4月呢？”在缺少年份时返回 `clarify`，不以 2018 年作为默认年份；“那2018年4月呢？”可从上一轮继承指标。
- 其他显式追问通过单次结构化补全请求尝试生成独立问题；严格解析模型 JSON、对新增数字做保守检查；无法确认时返回澄清。对不完整的单笔预测输入可尝试继承当前会话中上一轮提出的部分特征，最终仍必须经过原 V1 五项特征校验。
- API JSON 明确 `charset=utf-8`，改善 Windows PowerShell 5.1 中文响应乱码问题。

## 使用示例（Windows PowerShell）

开启服务的窗口（每次重启清除内存会话）：

```powershell
$env:DATAPILOT_V3_ENABLED="1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开 PowerShell 窗口执行以下两轮请求。真实请求需要 DeepSeek 模型调用，可能产生 API 费用。

```powershell
$uri = "http://127.0.0.1:8000/api/v3/chat"

$body1 = @{ question = "查询2018年3月的订单延期率" } | ConvertTo-Json -Compress
$first = Invoke-RestMethod -Uri $uri -Method POST -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body1))
$first | ConvertTo-Json -Depth 10

$body2 = @{ question = "那4月呢？"; conversation_id = $first.conversation_id } | ConvertTo-Json -Compress
$second = Invoke-RestMethod -Uri $uri -Method POST -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body2))
$second | ConvertTo-Json -Depth 10

$second.effective_question
$second.context_used
$second.tool_result.data.rows | Format-Table -AutoSize
```

预期同一 `conversation_id`，`$second.effective_question` 含 **2018年4月** 并延续原指标，`context_used=True`。实际工具选择与业务数值须按结果核对；模型路由不是硬编码的准确性证明。

重置同一用户的会话：`{ "question": "新的完整问题", "conversation_id": "<旧 UUID>", "reset_context": true }`；响应会返回一个**新**的 UUID。携带已经过期、重启后消失或其他不存在的 UUID 返回 HTTP `409`，客户端应开启新会话，不能被静默分配新 ID。

## 安全和设计局限

1. **仅限本机单进程**：多个 Uvicorn worker 之间不共享上下文。重启丢失会话，过期/淘汰会话无法恢复。后续若做持久化，应引入鉴权、隔离、数据库/Redis 和会话加密策略。
2. **未认证会话 ID**：UUID 仅为引用标识，不是登录态；现有安全前提仍是 V3 默认关闭且仅允许 `127.0.0.1` / `::1` 请求。不要暴露到局域网/公网，也不要记录或公开会话 ID。
3. **语义正确性非形式化保证**：月份型追问在条件唯一时可确定性替换；其他语言补全由模型提出，可能理解错误。结果显式提供 `effective_question` 供用户复核。补全过程不取得 SQL/业务结果，不会扩大 SQL 表列授权，真实执行仍走 V2 的安全过滤、日期/放大风险审查和资源限制。
4. **并非全功能 Agent**：每轮仍至多执行**一个**工具；未实现跨工具多步规划、SQL 自动修复、长期跨进程记忆、生产级鉴权/限流。短文本预测特征追问采用模型补全，不能保证每种自然语言变体都能成功。
5. **成本边界**：首轮一般一次路由调用（SQL 还需一次生成）；明确年月追问无需单独补全模型调用，但依然需要路由和 SQL 生成；其余追问可能额外增加一次补全调用。离线测试不请求 DeepSeek。

## 验收顺序

在项目根目录、开发分支中：

```powershell
python -m pytest tests/test_v3_conversation.py tests/test_v3_conversation_api.py -q
python -m scripts.v3_stage2_smoke
python -m pytest tests -q
```

第一项新增 **35 个**离线确定性测试；第二项使用模拟的模型决策驱动你机器中的**真实 LangGraph + V2 SQL 安全执行器 + Olist SQLite**，要求 2018-03/2018-04 有效日期已交付订单分别是 7003/6798；不调用外部 API；第三项是全项目回归，若此前 260 项的基线不变，预计 **295 passed**、48 subtests passed。请以本机真实输出为准。

## Git 提交

```powershell
git add app/v3_api.py app/v3_conversation.py
git add tests/test_v3_conversation.py tests/test_v3_conversation_api.py
git add scripts/v3_stage2_smoke.py docs/V3_STAGE2_CONVERSATION.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat(v3): add bounded multi-turn conversation context"
git push github feat/v3-multi-tool-agent
```

本次补丁只应包含上述六个文件。不要提交 `.env`、`*.bak`、SQLite、训练模型或此前基准日志。
