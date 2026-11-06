# 社区快递驿站包裹管理系统——核心原型 v2

本版本以“整体分层结构＋主要功能可运行实现”为目标。使用 Python 3.10+、SQLite 和标准库 HTTP 服务，无需安装第三方包。核心逻辑不是空函数；业务通过 HTTP、服务层、仓储接口和持久化贯通。

## 先运行这两条

```bash
# 在仓库根目录执行
python3 -m unittest discover -s tests -v
python3 demo_workflow.py
```

第一条执行全部57项测试（含已合并 PR #1 的编号校验回归）；第二条在临时目录中创建数据库和本地通知信箱，启动真实HTTP服务器，演示登录、入库重试、查询、通知、取件、异常件退回、统计、CSV导出和审计，完成后自动清理临时数据。演示账号密码只用于该临时流程，令牌在输出中隐藏。

## 持续运行接口服务

```bash
python3 main.py init
# 按提示设置管理员 admin 的密码（至少8位）
python3 main.py serve
```

访问 `http://127.0.0.1:8000/api/health` 检查服务。通过 `POST /api/auth/login` 获取 Bearer token，再调用其他接口。完整请求样例见 `examples/api.http`，接口机器可读说明见 `docs/openapi.json`。

另开终端投递一批入库通知：

```bash
python3 main.py worker --limit 20
```

通知写入 `data/mailbox.jsonl`，本地教学取件码可从该文件查看；并未调用真实短信。也可以在管理员会话中调用 `POST /api/notifications/dispatch`。本原型默认绑定本机地址，前端调用模块按同源服务或开发代理方式集成。

数据库默认 `data/station_core.db`，与原原型数据库分开。检测到旧版表结构时会明确拒绝，不覆盖已有记录。本次交付不含已初始化账号或运行数据库。

## 整体结构

| 层次／入口 | 文件 | 责任 |
|---|---|---|
| 启动与组装 | `main.py`、`src/bootstrap.py` | CLI、依赖组装、服务器启动 |
| 表现层 | `src/presentation/http_api.py` | 17个HTTP操作、JSON解析、状态码、CSV输出 |
| 应用层 | `src/application/services.py` | 登录、权限、业务校验与用例编排 |
| 核心接口 | `src/application/ports.py` | 仓储、身份存储、通知队列、网关、时钟契约 |
| 领域层 | `src/domain/models.py`、`security.py` | 状态、角色、分页、业务错误、密码与取件码摘要 |
| 数据访问层 | `src/infrastructure/sqlite_repository.py` | SQL、事务、唯一约束、审计、Outbox、会话 |
| 表结构 | `src/infrastructure/schema.sql` | 6张表与必要索引 |
| 外部服务适配 | `src/infrastructure/notifications.py` | 通知投递、失败退避、租约与本地信箱 |
| 前端调用边界 | `web/api_client.mjs` | 可被页面组件调用的登录与业务API客户端 |
| 共享模块 | `shared_modules/parcel_csv.py` | 被HTTP导出和原共享示例复用的CSV模块 |
| 运维工具 | `tools/backup_db.py` | 在线一致性备份与完整性校验 |
| 测试与演示 | `tests/`、`demo_workflow.py` | 业务回归、并发、真实HTTP联调 |

`src/contracts.py` 保留原 EPM-017 的领域数据类与可信Python兼容入口；`src/sqlite_repository.py` 是新仓储的兼容导入。正式HTTP请求统一经过新应用层，不直接调用旧的免取件码接口。原21项测试（含 PR #1 回归）和共享模块示例继续可运行。

## 已实现的主要功能

1. 登录与角色权限：管理员、站长、工作人员、审计员；密码使用带盐PBKDF2摘要；不透明会话令牌在数据库中只存SHA-256摘要，8小时过期。
2. 包裹入库：运单唯一；幂等范围为操作员＋请求键；同键不同原始参数返回冲突；包裹、审计和通知事件在同一事务提交。
3. 查询与货架：按运单、收件人标识、货架、状态筛选，支持分页、详情及在库货架调整。
4. 核验取件：操作员登录后核对收件人标识与六位取件码；重复出库不重复记审计；连续5次错误码锁定，由管理员或站长解除。
5. 异常件：登记损坏、地址错误、拒收、丢失等异常；处理后恢复在库或退回承运商；异常和退回状态禁止取件。
6. 滞留与业务统计：按入库时间计算滞留在库包裹，统计四种业务状态与滞留数量。
7. 通知补偿：事务Outbox、租约领取、失败退避、3次失败进入dead状态；网关使用事件键防止本地重复写信。派送为至少一次语义。
8. 审计、导出和备份：关键状态变化记录操作者；CSV只导出包裹编号、运单、货架、状态，并处理公式前缀；备份使用SQLite backup API。

## 数据库备份

```bash
python3 tools/backup_db.py data/station_core.db data/backups/station-001.db
```

目标备份文件必须不存在。工具会校验数据库完整性，不直接复制仍可能有WAL的数据库文件。

## 实现范围

本交付是能运行的核心原型，未制作完整业务页面、对接真实短信或进行万用户负载测试。`web/api_client.mjs` 提供前端对接代码，不宣称完整前端已完成。SQLite、标准库HTTP服务器和本地信箱用于课程验证；生产环境仍需要服务框架、TLS、登录限流、数据库迁移、多网点隔离、网关密钥管理及通知数据保护。信箱和Outbox包含演示手机号及取件码，只应使用测试数据。通知发送租约为60秒，实际网关应保证调用超时小于租约并使用稳定事件键。

验收范围、源文件位置、17个操作的参数、工作项映射及可用于报告的核心实现说明见 `docs/核心代码与接口说明.md`。原历史测试结果仍保留，新增证据文件名带 `core_v2`，不覆盖原三次提交和共享模块交接记录。

## 项目成员对应表

| 代号 | 职务 | 姓名 |
|---|---|---|
| PM | 项目经理 | 焦裕斐 |
| BA | 需求分析师 | 陈思远 |
| TL | 架构负责人 | 林子衡 |
| BE | 后端开发工程师 | 周启航 |
| FE | 前端开发工程师 | 许佳宁 |
| QA | 测试工程师 | 赵雨桐 |
| DO | 运维工程师 | 孙嘉诚 |

平台负责人使用实际操作账号，历史提交作者保留原始标识；角色分工不构成独立多人开发或审查的证明。

## GitHub 版本与历史

本次 v2 在 GitHub 现有 main 分支上追加提交，保留全部原提交及 [PR #1](https://github.com/lars-yu/ParcelManagementSystem/pull/1) 的审查、讨论与合并记录。升级前版本保存在 `backup/pre-core-v2-20261106` 备份分支。

压缩包中的本地 Git 历史不替换远端历史；PR #1 的正整数取件编号校验及其回归测试继续保留。协作文档沿用仓库目录：[共享模块交接说明](docs/共享模块交接说明.md)、[PR 说明模板](docs/PR说明模板.md)。当前版本的完整回归输出见 [57 项测试记录](evidence/github_core_v2_tests.txt)，历史验证记录见 [证据索引](evidence/README.md)。
