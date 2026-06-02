## 接续 DevFlowCI（复制到新 Chat 第一条）

- **仓库**：`c:\Users\ava_t\Desktop\DevFlowCI`
- **请先读**：`HANDOFF.md` → `CURRENT_STATUS.md` → 最新 `sessions/*/summary.md`
- **规则**：遵循 `.cursor/rules/session-handoff.mdc`；未明确要求不要 git commit

### 本 Chat 唯一目标

（只写一件事，例如：「为 iterate 补集成测试」）

### 不要重复做

- 
- 

### 关键路径

- 

---

运行 checkpoint 刷新上述文件：

```powershell
scripts\session_checkpoint.bat -NextGoal "你的目标"
```
