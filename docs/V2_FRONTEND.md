# DataPilot V2 · 本地网页工作台

新增 `/v2` 作为独立页面；保留原 `/` V1 Agent 的聊天、报表和预测功能。

## 安装

从本项目根目录执行：

```powershell
python scripts/install_v2_frontend.py
python -m unittest discover -s tests -p "test_v2_frontend.py" -v
python -m pytest tests/test_api.py tests/test_v2_api_integration.py -q
```

安装程序仅对 `app/main.py` 追加 `/v2` 页面路由，并在 `frontend/index.html` 的 header 中增加一个跳转链接。首次安装保存两个原样备份，不覆盖现有 V1 逻辑。重复执行安装程序不会重复插入代码。备份文件不提交 Git。

## 本机试用

在启动 Uvicorn 的 PowerShell 窗口中设置：

```powershell
$env:DATAPILOT_V2_ENABLED="1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/v2`。原版主页 `http://127.0.0.1:8000/` 仍然可用。

每次点击「生成并查询」将向 `/api/v2/query` 发起**一次新的真实 DeepSeek 请求**，可能产生费用。测试前请先确认服务已经启动，API Key 配置正确。V2 查询默认关闭，且仅允许本机访问。请勿将已启用的试验服务绑定到 `0.0.0.0` 或部署至公网。

## 前端状态与安全

- `ok`：显示表格和 SQL，但不宣称语义已证明正确。
- `partial`：显示截断提醒，不将结果作为完整统计。
- `needs_review`：仅显示风险详情和 SQL，不展示未经执行的业务表格。
- `clarify` / `unsupported`：显示服务建议，避免伪造数据。
- `error`：展示安全错误信息，不再自动重试。
- DOM 内容全部使用 `textContent`，不渲染不可信 HTML，不使用第三方 CDN。
- 前端只展示一次一问结果，不实现多轮对话、自动恢复或统计正确性保证。

## 验收建议

1. 打开 `/` 并确认 V1 原页面可用，首页存在 V2 入口。
2. 打开 `/v2`，使用 `2018年3月购买且已交付的订单有多少笔？`，预期表格有一行，值为 7003。
3. 展开 SQL 并确认完整文本可以阅读。
4. 在测试环境通过模拟响应覆盖 `needs_review`、`partial`、`clarify`、`unsupported` 和 `error` 显示路径。
5. 运行旧的 `tests/test_api.py`，确认 V1 接口回归通过。

## Git 提交

```powershell
git add frontend/v2.html frontend/index.html app/main.py scripts/install_v2_frontend.py tests/test_v2_frontend.py docs/V2_FRONTEND.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat: add local V2 text-to-sql web workspace"
```

请勿追踪 `*.before_v2_frontend.bak`、`*.before_v2_api.bak`、`.env`、`hard8_first_run.zip`、`holdout12_first_run.zip` 等本地文件。
