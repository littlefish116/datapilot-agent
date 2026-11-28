# DataPilot V3.2 · 定向月份回答修复

## 复现与原因

- 真实请求「查询2018年3月的订单延期率」→「那4月呢？」正确解析为 `查询2018年4月的订单延期率`，但 `business_report` 的原始返回包含整个历史月度报表，V3.1B 的 `_answer_from_result` 无条件选取 `rows[:5]`，所以回复展示 2016 年的前五行。
- 这是**最终回答的证据选择错误**，不是多轮问题解析错误，也不是 SQL 结果错误。

## 修复

新增 `app/v3_answer_policy.py`，仅在 `(tool=business_report, report_id=monthly_late_delivery)` 且最终问题明确只有一个 `YYYY年M月` 时，对 `data.rows` 查找唯一相应 `purchase_month=YYYY-MM`，使用已查询出的 `delivered_with_dates`、`late_orders`、`late_rate_pct` 构造单月回答。

- 校验字段类型、范围和计数与百分比一致性；若异常，**不展示错误数据**。
- 没有匹配月份时，完整报表与截断报表采用不同说法；截断时不会声称该月没有数据。
- 多月、同比、环比、比较请求仍走原回答路径；本补丁不实现自动比较或语义推理。
- 不修改 `tool_result` 或数据库，不修改 V1/V2 安全机制，不重新调用 LLM，不自动修复 SQL。
- 仅为简单月份固定报表做定向呈现。更复杂的联合口径/额外筛选仍需路由与 SQL Gold 专项评估，不能据此声称已完全解决。

## 安装与测试（项目根目录，V3.2 补丁已经解压的前提下）

```powershell
Expand-Archive -LiteralPath "D:\MyDemo\DataPilot_V3_Stage2_Targeted_Answer_Fix.zip" -DestinationPath . -Force
python scripts/install_v3_targeted_answer.py
python -m pytest tests/test_v3_targeted_answer.py tests/test_v3_conversation.py tests/test_v3_conversation_api.py -q
python -m scripts.v3_stage2_answer_smoke
python -m pytest tests -q
```

**预期**：先检查新测试通过；真实冒烟测试显示 2018 年 3 月 **18.96%**（7003 笔）、4 月 **4.50%**（6798 笔）；原完整报表结果保持未变。隔离环境没有 LangGraph 时集成测试可能被 skip，但用户本机必须跑过冒烟测试才能验收。

实际 API 测试需先 Ctrl+C 停止旧 Uvicorn，再按原命令重启；如果仍在运行旧进程，不会加载修改后的路由模块。新请求使用新会话，不依赖已过期的会话 ID。

## Git 范围

除 V3.2 原计划的六个文件外，本修复额外新增 5 个文件：`app/v3_answer_policy.py`、`scripts/install_v3_targeted_answer.py`、`tests/test_v3_targeted_answer.py`、`scripts/v3_stage2_answer_smoke.py` 及一份文档（共 5 个文件），并通过安装脚本修改 `app/v3_routing.py`。不要提交安装产生的 `.bak`。
