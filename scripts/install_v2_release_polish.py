"""Idempotent surgical V2 release polish; does not overwrite V1 components.

Usage (from repo root): python scripts/install_v2_release_polish.py
Requires V2 API integration already committed. Saves a local .bak file.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
RUNTIME = ROOT / "app" / "v2_runtime.py"

V2_README_SECTION = """## 当前版本：V2 本地 Text-to-SQL 工作台

> V2 已实现；以下原有 V1 章节保留作为项目历史基线，最新 V2 状态及局限以本节和 `docs/V2_RELEASE_NOTES.md` 为准。

- **V1**：`/` 对话页面，支持固定报表及已训练的 HGB 延期风险评分工具；预测分数未经概率校准。
- **V2**：`/v2` 单轮数据分析页面，调用 `POST /api/v2/query`，支持自然语言转 SQL、SQLite 只读安全执行、语义风险提示、结果表格和 SQL/Token/耗时展示。
- **权限与风险**：V2 默认关闭，启用后仅允许本机访问；仅授权部分 Olist 数据表。`no_known_risk` 不代表业务语义得到证明；无自动 SQL 修复、不支持 V2 多轮上下文。
- **本地启动**（先准备好数据、模型、`.env` 和依赖）：

  ```powershell
  $env:DATAPILOT_V2_ENABLED="1"
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```

  浏览器访问 `http://127.0.0.1:8000/v2`；V1 仍在 `http://127.0.0.1:8000/`。每次 V2 查询可能产生模型 API 费用。
- **当前综合回归**：2026-11-28 本地 `python -m pytest tests -q`：176 passed、48 subtests passed、17 条 LangSmith 弃用警告（非失败）。
- **研究结果**：Holdout 12 自动 4/8 SQL、人工审计 8/8 SQL；Hard8 自动 2/8 双数据库、后验人工审计 7/8；X08 原始执行超出 500 万 VM 指令预算，人工过滤下推候选在原预算内通过；X03 另有日期有效性边界失败。完整口径见 `docs/V2_RELEASE_NOTES.md`。
- **后续 V3**：将 V2 Text-to-SQL 与已有预测模型统一为多工具 Agent，增加多轮上下文和受控 SQL 自动修复（V2 未实现）。

"""


def polish_runtime(source: str) -> str:
    imp = "from app.v2_response_policy import normalize_unsupported_message\n"
    anchor = "from app.v2_text_to_sql import generate_sql_decision\n"
    if imp not in source:
        if source.count(anchor) != 1:
            raise ValueError("v2_runtime.py 结构与已验收的版本不一致，拒绝修改")
        source = source.replace(anchor, anchor + imp, 1)
    old = '    if decision["status"] != "sql":\n        return response\n'
    new = (
        '    if decision["status"] != "sql":\n'
        '        if decision["status"] == "unsupported":\n'
        '            response["message"] = normalize_unsupported_message(question, response["message"])\n'
        '        return response\n'
    )
    if new not in source:
        if source.count(old) != 1:
            raise ValueError("v2_runtime.py 分支结构与已验收的版本不一致，拒绝修改")
        source = source.replace(old, new, 1)
    return source


def polish_readme(source: str) -> str:
    anchor = "## 1. 功能概览\n"
    marker = "## 当前版本：V2 本地 Text-to-SQL 工作台"
    if marker in source:
        return source
    if source.count(anchor) != 1:
        raise ValueError("README.md 缺少预期章节标题，拒绝修改")
    return source.replace(anchor, V2_README_SECTION + anchor, 1)


def install() -> None:
    targets = ((RUNTIME, polish_runtime), (README, polish_readme))
    planned = []
    for path, transform in targets:
        if not path.is_file():
            raise FileNotFoundError(f"缺失正式文件: {path}")
        original = path.read_text(encoding="utf-8-sig")
        updated = transform(original)
        planned.append((path, original, updated))
    for path, original, updated in planned:
        if original == updated:
            print(f"Unchanged: {path.relative_to(ROOT)}")
            continue
        backup = path.with_name(path.name + ".before_v2_release.bak")
        if backup.exists():
            raise FileExistsError(f"备份已存在，拒绝覆盖: {backup}")
        backup.write_text(original, encoding="utf-8")
        path.write_text(updated, encoding="utf-8", newline="\n")
        print(f"Updated: {path.relative_to(ROOT)} (backup: {backup.name})")


if __name__ == "__main__":
    install()
