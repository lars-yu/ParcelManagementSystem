# 验证记录索引

所有命令从仓库根目录执行。历史记录保留原始输出，其中旧提交号和测试数量对应当时版本。

| 文件 | 内容与口径 |
| --- | --- |
| `test_results.txt` | 最初16项核心业务测试的历史输出 |
| `shared_export_tests.txt` | 共享模块4项专项验证的历史输出 |
| `shared_export_demo.csv` | 共享模块演示输出，不包含收件人身份 |
| `pr_pickup_id_tests.txt` | PR #1的21项完整回归，包含取件编号校验 |
| `structure_test_results.txt` | 本次目录整理后的21项完整回归 |
| `git_log.txt` | 最初核心业务开发的历史提交图 |
| `shared_module_git_log.txt` | 共享模块交接的历史提交图 |
| `shared_module_manifest.json` | 交接时的来源与角色清单，提交号为历史值 |
| `plan_checks.txt` | 实验材料中的项目计划核对记录 |
| `dashboard_checks.txt` | 实验材料中的离线仪表盘核对记录 |

提交说明改为中文后提交号发生变化，当前来源以[共享模块交接说明](../docs/共享模块交接说明.md)及Git历史为准。历史日志与清单保留供追溯，不代表当前分支状态。

PR创建、自查、意见处理与合并记录见[PR #1](https://github.com/lars-yu/ParcelManagementSystem/pull/1)。该流程为本人按项目职责进行的单人角色演练，PingCode关联仍待实际执行。
