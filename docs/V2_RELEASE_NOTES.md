# DataPilot V2 · Release Notes (draft)

> 状态：候选版本（release candidate），尚未创建 `v2.0.0` Git 标签。历史实验数据与此次发布收尾应分开记录。

## V2 新功能

- `/v2` 单轮中文自然语言数据查询界面；`POST /api/v2/query` 独立于 V1 `/api/chat`。
- DeepSeek 提出 SQLite `SELECT`，由 SQLGlot AST 检查、表/列/函数白名单、SQLite Authorizer 与只读连接限制执行；有 VM 指令、时间和结果大小预算。
- 业务歧义可回复 `clarify`；未开放数据可回复 `unsupported`；启发式语义风险可回复 `needs_review` 并停止执行；结果截断显示 `partial`。
- 显示生成 SQL、结果表格、Token 和耗时。V2 默认关闭，启用后仅允许本机请求，无生产级认证或请求配额。
- V1 的固定报表、HGB-V1 未校准延期风险评分和多轮 Agent **保持独立可用**。V2 不具备多轮状态继承、自动 SQL 修复或对预测模型的统一路由。

## 已完成验证（2026-11-28）

- 全项目 `python -m pytest tests -q`：**190 passed, 48 subtests passed, 17 warnings**（LangSmith `asyncio.iscoroutinefunction` 弃用警告）。
- V2 浏览器实测：已交付订单查询返回 7,003；模糊“哪个州的销售表现最好”请求澄清；未开放评论数据请求返回不支持；多表聚合 Top-5 正常返回，但该次结果**尚未逐项与 Gold 独立核对**。
- API 单测中风险拦截与检查器故障时停止执行路径通过。真实浏览器各异常状态的完全手工覆盖尚未全部证明。

## 数据分析实验：不同成绩绝不能混写

| 实验 | 首次自动评分 | 人工审计/后验诊断 | 解释 |
|---|---|---|---|
| Holdout 12 | SQL 4/8 自动通过，4/8 待字段映射复核 | 8/8 SQL 后验核实；另有 4 项行为测试 | 人工映射不是模型重试 |
| Hard8 | 双数据库 2/8 自动通过；5/8 review；1/8 fail | 7/8 双数据库通过，X08 首次真实库失败 | 定向压力题集，不代表总体准确率 |
| X08 | 首次真实库超过 500 万 VM 指令预算 | 扩展预算下首次 SQL 与 Gold 结果吻合；人工 Filter-First 候选在原预算内两库通过 | 不算自动修复，首次失败保持失败 |
| X03 | 原始 Hard8 两库均正确 | 新构造异常日期时：Gold 2 笔/50%；模型 SQL 4 笔/25% | 已识别日期有效性语义风险 |

相关技术文档：`docs/V2_POSTHOC_AUDIT_AND_BOUNDARIES.md`、`docs/V2_HARD8_SEMANTIC_ROBUSTNESS.md`、`docs/V2_STAGE2C_DIAGNOSTICS.md`、`docs/V2_STAGE2C_CONTROLLED_RECOVERY.md`。

## 局限及 V3 方向

- 提示词不构成 SQL 授权；允许表不等于 Olist 全部原始表（原始数据库的 `order_reviews` 存在，但 V2 不授权访问）。
- 语义风险检测是启发式的，不是形式化正确性证明；可能漏报或误报。
- 无公开部署所需的身份认证、限流、日志脱敏、API 调用预算与多租户隔离；只适用于可信本机数据库。
- V1 HGB 模型的输出是未经概率校准的风险评分，不能视为真实延期概率。
- V3 计划：统一 SQL、报表与 ML 预测工具；增加多轮状态管理、受控自动 SQL 恢复和可验证的综合分析。
