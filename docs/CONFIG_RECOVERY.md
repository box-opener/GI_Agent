# BetterGI 配置备份与手动恢复

`bgi_controller.py` 写入 BetterGI JSON 配置时，会将同一次任务涉及的文件作为一个事务处理：

1. 保存修改前的原始文件；
2. 保存每个文件的统一 Diff；
3. 原子写入新配置；
4. 回读并校验 JSON 内容；
5. 若写入或回读失败，自动恢复本次已写入的文件。

默认的事务记录目录为：

```text
<BGI_DIR>/User/GI_AgentBackups/
```

可以在 `.env` 中通过 `BGI_BACKUP_DIR` 覆盖此位置。

## 查看历史事务

在 `GI_Agent` 根目录、并启用项目 Python 环境后执行：

```powershell
python -m skills.config_recovery list
```

输出中的第一列是事务 ID。查看某次事务的涉及文件、状态和 Diff 路径：

```powershell
python -m skills.config_recovery show <事务ID>
```

## 手动恢复

### 在 `main.py` 中恢复（推荐）

启动 CLI Agent 后，在普通输入提示或任务审批提示中输入：

```text
rollback
```

程序会列出历史事务；输入目标事务 ID 后，核对将被恢复的文件，并输入 `RESTORE` 二次确认。也可以直接输入：

```text
rollback <事务ID>
```

该命令由 CLI 本地处理，不会发送给 LLM，也只会恢复 BetterGI 本地配置，不会撤销已发生的游戏内操作。

### 独立终端恢复

恢复某次**已提交**事务之前的配置：

```powershell
python -m skills.config_recovery restore <事务ID>
```

命令会展示风险提示，只有输入 `RESTORE` 才会继续。非交互式自动化场景可显式传入：

```powershell
python -m skills.config_recovery restore <事务ID> --yes
```

手动恢复也会创建新的备份、Diff 和 `manifest.json`。因此，如果恢复后发现选择了错误的事务，可对新生成的 `recovery-...` 事务再次执行 `restore`，将配置恢复回手动恢复前的状态。

## 注意事项

- 恢复会覆盖当前配置；执行前请先使用 `show` 确认目标事务和文件。
- 建议先关闭 BetterGI，避免其同时写入同一配置文件。
- 自动回滚仅处理当前事务已经写入的文件；手动恢复用于处理已成功提交、但事后需要撤销的任务。
