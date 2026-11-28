# DataPilot V2 本地实验接口

## 范围

新增 `POST /api/v2/query`，**不修改** V1 `/api/chat`、`/api/reports` 和 `/api/predict` 的既有行为。
V2 是独立的、单轮 Text-to-SQL 查询，不具备多轮历史、自然语言结果总结和自动 SQL 修复。

**仅供本地开发演示，不要对公网开放。** 默认关闭，且校验 ASGI 连接来源为 `127.0.0.1` 或 `::1`。
现阶段没有接口身份认证、请求配额、账单预算、完整的业务语义证明或生产级安全审计。

## 安装

项目根目录解压补丁后，运行：

```powershell
python scripts/install_v2_api.py
python -m pytest tests/test_v2_api_integration.py -q
python -m unittest discover -s tests -p "test_v2_safe_sql.py" -v
python -m unittest discover -s tests -p "test_v2_text_to_sql.py" -v
```

`install_v2_api.py` 仅在 `app/main.py` 增加一条 import 和 `app.include_router(v2_router)`，并生成本地备份 `app/main.py.before_v2_api.bak`。备份无需提交 Git。若主文件已变更，脚本拒绝猜测性修改。

## 本地调用 DeepSeek（显式启用，收费）

在项目根目录的 PowerShell 会话中设置：

```powershell
$env:DATAPILOT_V2_ENABLED="1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

新开一个 PowerShell 窗口：

```powershell
$body = @{ question="2018年3月购买且已交付的订单有多少笔？" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v2/query" -Method POST -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 12
```

DeepSeek 配置仍使用已有 `.env` 的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。真实调用会产生成本。

## 返回状态和信任边界

- `ok`：一次模型生成，安全执行器执行成功；**不保证业务语义必然正确**。
- `partial`：安全执行器截断结果，不能把展示行视为完整结果。
- `clarify`：模型需要进一步确定业务口径；不执行 SQL。
- `unsupported`：模型判断超出支持范围；不执行 SQL。
- `needs_review`：命中了启发式语义风险，SQL 供人工复核，**不执行**。
- `error`：模型生成或权限执行失败；保留安全分类信息，不自动重试。

所有 SQL 必须经 `app/v2_safe_sql.py` 再验证，并使用只读 SQLite 权限层。语义检查器 `app/v2_semantic_risk_guard.py` 是保守启发式检查，可能误报；无提示不证明查询完全正确。不要将接口开放到未受控的公共网络。

**首次实验/Hard8 记录不变。** 本功能不把 X08 人工优化候选冒充自动修复。

## 提交建议

```powershell
git status --short
git add app/v2_api.py app/v2_runtime.py scripts/install_v2_api.py tests/test_v2_api_integration.py docs/V2_API_INTEGRATION.md app/main.py
git diff --cached --check
git diff --cached --stat
git commit -m "feat: expose local opt-in v2 query endpoint"
git push github feat/v2-text-to-sql
```

注意：不要提交 `.env`、数据库、临时备份 `app/main.py.before_v2_api.bak` 或实验 ZIP。
