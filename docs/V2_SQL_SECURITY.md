# DataPilot V2 · 安全 SQL 执行器（Stage 2A）

## 本次交付

这份**增量补丁**基于原仓库 `app/sql_tools.py` 和 SQLite `sql/schema.sql` 设计，**不修改 V1 Agent、FastAPI、数据库或 Benchmark**。

- `app/v2_sql_policy.py`：表 / 字段 / 函数白名单及执行预算。
- `app/v2_safe_sql.py`：SQLGlot AST 校验、SQLite 只读执行、authorizer、progress handler、结果限制与结构化错误。
- `tests/test_v2_safe_sql.py`：11 项合成数据库单元测试；不需要 Olist 原始 CSV 或 LLM。
- `evaluation/v2_sql_smoke.py`：真实 SQLite 数据库的只读冒烟测试。
- `evaluation/v2_gold_smoke.py`：将 V1 评测的 6 条 Gold SQL 交给 V2 安全执行器。
- `requirements-v2.txt`：本阶段新增依赖，保留 V1 `requirements.txt` 不变。

## 本地操作（Windows PowerShell）

项目根目录应为 `D:\MyDemo\项目\datapilot-clean-check`。

1. 确认之前的 Benchmark 代码已提交。当前应处于 `feat/v1-agent-benchmark` 或其后续分支；执行 `git status` 确认状态。建议在已提交的 Benchmark 上创建新分支：

   ```powershell
   git switch feat/v1-agent-benchmark
   git switch -c feat/v2-text-to-sql
   ```

   如果已经存在 `feat/v2-text-to-sql`，直接 `git switch feat/v2-text-to-sql` 即可。

2. 将本 ZIP **解压到项目根目录**。只新增文件，不覆盖现有 V1 代码：

   ```powershell
   Expand-Archive -Path "$HOME\Downloads\DataPilot_V2_SafeSQL_Stage2_Patch.zip" -DestinationPath . -Force
   ```

3. 安装 SQLGlot：

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements-v2.txt
   ```

4. 单独测试 SQL 执行器（不需要运行 Uvicorn，也不调用 DeepSeek）：

   ```powershell
   python -m unittest discover -s tests -p "test_v2_safe_sql.py" -v
   ```

5. 使用 Olist 真实数据库做冒烟检查：

   ```powershell
   python -m evaluation.v2_sql_smoke --db data/processed/olist.db
   python -m evaluation.v2_gold_smoke --db data/processed/olist.db
   ```

6. 保存测试输出，执行 `git diff --check`、`git status`，确认无误后再 `git add app/v2_sql_policy.py app/v2_safe_sql.py tests/test_v2_safe_sql.py evaluation/v2_sql_smoke.py evaluation/v2_gold_smoke.py docs/V2_SQL_SECURITY.md requirements-v2.txt`，然后提交。**不要把数据文件或 API Key 提交到仓库**。

## 安全边界（务必理解）

1. SQLGlot 检查**只能**视为第一道筛选。禁止多语句、非 SELECT 语句、递归 CTE、`SELECT *`、跨数据库表和未授权数据表；允许单条 SELECT 中的 JOIN、CTE、聚合和窗口函数。
2. SQLite `mode=ro` + `PRAGMA query_only=ON` 防止直接改动主数据库。`set_authorizer` 对真实表、列和函数的读取作 **deny-by-default** 检查。`set_progress_handler` 对 VM 指令数和执行时间提供预算限制。输出行数最多 50 行，大小最多 64 KiB（默认）。
3. **业务含义仍需校验**：SQL 可能成功运行、没有越权，但由于 JOIN Fan-out / 分母口径等原因算错指标。本模块不替代指标语义字典和 Benchmark。
4. **不是完整数据脱敏 / 租户安全方案**：部分关联键仍允许在查询内部使用。AST 对原始键列的输出限制只是浅层检查，无法阻止全部别名、表达式、编码或推断泄露。此模块只供 **本地可信开发与评测** 使用；在引入无认证的公共 API 或不可信用户前，应采用脱敏聚合视图 / 独立分析库、更细的数据访问策略，并进行安全审计。
5. 结果上限只限制返回数据，不能保证 SQL 本身廉价；执行资源限制可降低风险，但不能作为可用性或完全隔离保证。
6. 不使用 `immutable=1`（它会让 SQLite 忽略文件变化和锁检测）；不执行用户编写的 `PRAGMA`、`ATTACH`、`DELETE` 等语句。
7. **当前未接入 DeepSeek / LangGraph / FastAPI**。用户问题也不会由此自动生成 SQL；这是特意设定的安全优先研发顺序。

## 验收条件

- 11 项单元测试全部通过（Windows 上确认 SQLite 文件正确关闭）。
- 冒烟样例 `status_counts`、`state_delivery` 返回 `status=ok`，`write_denial`、`schema_denial` 返回 `status=error`。
- 六条 Gold SQL 全部 `status=ok`，与 V1 的 `references.json` 中 D01–D06 的结果口径一致；如果被拦截，应先诊断并修正白名单，不要直接关闭 authorizer。

## 下一步

若以上验收满足，再接入 Text-to-SQL 的生成器：只输出 SQL 字符串 → 调用 `execute_safe_sql()` → 结构化结果 → 和参考答案比较。将来可以增加有限 SQL 修复，但仅对语法/字段等适当错误重试，绝不能绕过访问限制。

## 验证状态

本补丁已通过 `python -m compileall` 静态语法检查；数据库层使用独立合成数据库验证了正常聚合、未授权表 / 函数、写入拒绝、结果长度拒绝。由于补丁生成环境不可安装 SQLGlot，**AST 相关单元测试、六条 Gold SQL 和 Windows 测试仍等待本机验证**。不要声称它们已经全部通过。
