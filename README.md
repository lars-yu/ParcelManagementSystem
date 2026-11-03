# 社区快递驿站包裹管理系统核心原型

## 项目成员对应表

| 代号 | 职务 | 姓名 |
| --- | --- | --- |
| PM | 项目经理 | 焦裕斐 |
| BA | 需求分析师 | 陈思远 |
| TL | 架构负责人 | 林子衡 |
| BE | 后端开发工程师 | 周启航 |
| FE | 前端开发工程师 | 许佳宁 |
| QA | 测试工程师 | 赵雨桐 |
| DO | 运维工程师 | 孙嘉诚 |

任务、资源表和代码交接按上述职责对应；平台负责人仍使用实际操作账号。

教师给定题目。工作项EPM-017，2026年10月27日实际实现和验证。

## 仓库结构

```text
ParcelManagementSystem/
├── README.md                 # 项目说明与运行入口
├── demo_shared_code.py       # CSV共享模块集成演示
├── src/                      # 数据模型、业务服务与SQLite仓储
├── shared_modules/           # 独立的CSV导出模块
├── tests/                    # 业务与共享模块自动化测试
├── docs/                     # 协作说明及PR模板
│   ├── 共享模块交接说明.md
│   └── PR说明模板.md
└── evidence/                 # 测试、演示与历史验证记录
    └── README.md             # 证据索引与历史口径说明
```

- [共享模块交接说明](docs/共享模块交接说明.md)：接口、角色职责和复现命令。
- [PR说明模板](docs/PR说明模板.md)：后续PR的填写参考。
- [验证记录索引](evidence/README.md)：区分历史测试记录与当前验证结果。

演示入口和测试命令保持不变：

```bash
python3 demo_shared_code.py
python3 -m unittest discover -s tests -v
```

## 运行

使用Python 3.10或更新版本，无第三方依赖。

```bash
# 在仓库根目录执行；从实验材料目录进入时先 cd parcel_project
python3 -m unittest discover -s tests -v
git log --all --graph --oneline --decorate
```

预期21项测试通过（原核心业务16项、取件编号校验1项，加共享模块4项），测试自动创建并清理临时SQLite数据库。原始结果见`evidence/test_results.txt`；本次取件编号校验回归结果见`evidence/pr_pickup_id_tests.txt`。

## 源码与规则

- `src/contracts.py`：InboundRequest、Parcel、ParcelRepository抽象接口和ParcelService。
- `src/sqlite_repository.py`：SQLite实现子类，事务内入库、幂等、核验出库和审计。
- `tests/test_parcel.py`：17项测试，包括16线程重复运单提交和12线程同键重试。

同一规范化运单只允许一条包裹记录。幂等范围是操作员与请求键；同键必须对应相同运单、收件人及货架。同一货架格可以存放不同包裹。

取件时传入收件人身份必须匹配包裹记录；重复取件不重复写出库审计。出库后旧入库键返回已出库记录，新键也不能重复登记同一运单。更换承运商或重新寄件时，生产系统需要明确规范化标识规则。

## 实现边界

本原型仅实现服务层及SQLite持久化，没有完整HTTP登录、短信取件码、通知服务、异常件和多网点隔离。传入身份必须由可信上层认证；仅比较两个身份字符串不能代替真实身份核验。SQLite一致性测试不证明万用户性能。

真实完成：创建分支、EPM-017三次提交、16项测试及本地非快进合并。

已增加：单人扮演BE、FE、QA的共享模块演练，见[共享模块交接说明](docs/共享模块交接说明.md)。远端PR与单人职责自查见[PR #1](https://github.com/lars-yu/ParcelManagementSystem/pull/1)，包含审查意见及处理记录。待补：PingCode关联及Project原生截图。请勿把PR模板当作已发生的审查记录。

## 取件编号校验改进

服务层要求包裹编号为正整数；布尔值、浮点数、字符串、空值及非正整数均抛出ValueError，避免SQLite将True、1.0或字符串“1”匹配到编号1。非法请求不改变包裹状态，也不增加出库审计。合法但不存在的编号仍抛出KeyError。
