# DataPilot V3.4 · 本机 Web 交互与可视化

## 基线与范围

- 基于 `feat/v3-multi-tool-agent` 提交 `0303953`；保留 V3.3B **真实 SQL 修复能力评测** 为独立质量任务，不在 V3.4 声称它已通过。
- 新增独立 `GET /v3` 页面和 `GET /v3/app.{js,css}`；前端继续调用既有 `POST /api/v3/chat`。无 V1/V2 服务端代码修改，无模型重新训练和数据库迁移。
- 使用原生 HTML/CSS/JavaScript + SVG（无外部 CDN）；页面本地内存保存会话 ID，刷新后开新会话，服务器端仍遵守现有内存会话过期和容量限制。
- 支持：多轮对话、澄清、错误、风险复核、部分数据、响应表格与柱形/折线图切换、未经校准的风险评分、展开 SQL/语义风险/修复审计。
- 表格最多展示前 100 行，图表绘制前 24 行，**不重新聚合**；完整数据是否已在后端截断，以 `tool_result.data.truncated` 为准。
- `monthly_late_delivery` 单月追问的回答聚焦指定月份，但底层 `tool_result.data.rows` 仍为完整月度报表，表格显示所有返回行；不是前端查询错误。

## 安装（PowerShell）

在项目根目录、当前 `.venv` 已激活的情况下：

```powershell
Expand-Archive -LiteralPath "D:\MyDemo\DataPilot_V3_Stage4_Web_UI_Patch.zip" -DestinationPath . -Force
python scripts/install_v3_web.py
python -m pytest tests/test_v3_web.py tests/test_v3_frontend_contract.py -q
python -m scripts.v3_stage4_smoke
python -m pytest tests -q
```

`install_v3_web.py` 只在 `app/main.py` 添加两行（import + include_router），创建 `app/main.py.before_v3_web.bak`；重复安装是 no-op。若目标结构异常或已存在同名备份，会拒绝修改。不要提交 `.bak`。

## 浏览器验收

请先停止旧服务，再在服务器端运行：

```powershell
$env:DATAPILOT_V3_ENABLED="1"
# 可选：默认仍关闭 SQL 自动修复（减少成本，并使界面验收可重复）
$env:DATAPILOT_V3_SQL_REPAIR_ENABLED="0"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后打开 http://127.0.0.1:8000/v3 。推荐依次验收：

1. 首轮输入「查询2018年3月的订单延期率」，期望回复 18.96%、7003 笔、1328 笔；可展开原始表格与趋势图。
2. 同一会话追问「那4月呢？」，期望显示 `effective_question`（实际查询）为 2018年4月、回复 4.50%、6798 笔、306 笔，显示「已继承上下文」。
3. 点击「新建会话」再问「那4月呢？」，期望要求提供完整上下文，不从旧会话偷用年份。新会话不会清空服务器的其他会话；历史只保留在当前页面。
4. 问「统计2018年3月每天的订单数量，返回订单数最多的前5天」，若模型选择 SQL 并成功执行，可查看5行表格、图表与实际 SQL。此请求会产生 DeepSeek 费用；模型输出不是确定的。
5. 问「评估一笔订单的延期风险：7月购买，星期二，2件商品，商品金额199.9，客户在SP州」，检查原始风险评分（例如 0.025889）被标识为**非概率**，不显示成实际延期概率百分比。预测值以本机模型为准。
6. 验证 404（未启用）、409（会话失效）、`clarify`、`needs_review`、`partial` 的 UI 呈现，以及表格/图表中没有把数据误认为完整结果。

可选：使用浏览器开发者工具检查 `Content-Security-Policy`、`Cache-Control: no-store` 和无第三方网络资源。**切勿将开启了 V3 的服务监听 `0.0.0.0`、暴露公网、配置公开反向代理。**

## 安全与已知限制

- 仅适合**单机受信任的历史 Olist 演示**。本机访问控制并非用户身份认证；没有真实鉴权、持久化会话、用量限流或生产级审计系统。
- 前端不通过 `innerHTML` 直接注入模型输出，图表使用原生 SVG 文本节点；同时响应采用限制性的 CSP。不会把聊天内容写入浏览器 localStorage。
- 前端依赖后端既有的 SQL AST/SQLite Authorizer、语义风险与单轮修复限制，不是 SQL 安全替代方案。
- 图表限于非负数值和能识别的分类维度，非适配表格只显示文本/表格。图表只表达返回的行，不宣称统计业务正确性。
- Web 浏览器可能与 PowerShell 呈现不同；浏览器端的真实功能需在用户本机验证。
- V3.3B：后续单独评测初次失败率、允许修复比例、修复成功率、Gold 语义一致率；不合并到 V3.4 通过率。

## Git（测试通过后）

```powershell
git add app/main.py app/v3_web.py frontend/v3.html frontend/v3.css frontend/v3.js
git add scripts/install_v3_web.py scripts/v3_stage4_smoke.py
git add tests/test_v3_web.py tests/test_v3_frontend_contract.py docs/V3_STAGE4_WEB.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat(v3): add local multi-turn analytics web UI"
git push github feat/v3-multi-tool-agent
```

避免使用 `git add .`，不要上传已有临时备份或离线实验数据。
