# V2.0.0 发布前检查清单

不在完成下列检查之前创建 `v2.0.0` 标签。

- [x] API、SQL 安全、生成、前端和 V1 整体回归：176 passed + 48 subtests passed（用户本地 2026-11-28）
- [x] 首次实际 V2 查询：7003 笔；模糊问题澄清；评论评分查询受限；前端 Top-5 正常返回
- [x] 保留 Holdout/Hard8 原始日志，人工审计和受控恢复独立记载
- [ ] **补齐 `requirements.txt` 中 `sqlglot` 精确版本**：运行 `python scripts/pin_v2_sqlglot.py`，自动将当前安装版本固定为 `sqlglot==对应版本`；再运行 `python scripts/check_v2_release.py`
- [ ] 完成 README 发布状态更新与评论权限拒绝文案的小范围测试；重跑全部测试
- [ ] 验证无 `.env`、密钥、数据库、实验 ZIP、备份文件被 Git 跟踪（`git status --short`、`git ls-files`）
- [ ] **全新虚拟环境**从 `requirements.txt` 安装，验证至少 `import sqlglot`、启动和 V2 API 主要回归；重建数据/模型的完整流程另有原 README 说明
- [ ] 核对 Top-5 多表金额的 Gold 对照结果（如要宣称具体业务计算正确）
- [ ] 代码与文档提交推送；将待发布提交打上注释标签 `v2.0.0`，推送该标签

## Windows PowerShell

```powershell
python scripts/install_v2_release_polish.py
python -m unittest discover -s tests -p "test_v2_release_polish.py" -v
python scripts/pin_v2_sqlglot.py
python scripts/check_v2_release.py
python -m pytest tests -q

git status --short
git diff --check
git diff --stat
```

提交建议：

```powershell
git add app/v2_response_policy.py app/v2_runtime.py scripts/install_v2_release_polish.py scripts/pin_v2_sqlglot.py scripts/check_v2_release.py tests/test_v2_release_polish.py docs/V2_RELEASE_NOTES.md docs/V2_RELEASE_CHECKLIST.md README.md requirements.txt
git diff --cached --check
git diff --cached --stat
git commit -m "docs: prepare v2 release and clarify unsupported data scope"
git push github feat/v2-text-to-sql
```

**标签命令（仅在剩余检查全部通过后）**：

```powershell
git tag -a v2.0.0 -m "DataPilot V2 local Text-to-SQL release"
git push github v2.0.0
```

标签应指向确定的发布提交；若仓库已经存在该标签，应检查，而不是强行重建或覆盖。尚不要求将分支合并到 `main` 才能创建标签；合并可以作为独立审查步骤。
