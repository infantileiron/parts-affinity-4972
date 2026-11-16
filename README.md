# 远航 · 维保备件联用发现

将维保作业中的备件集合变成可解释的联用规则，并让维修策划组核对兼容性后形成备件包方案。

## 场景和业务模块

远航设备服务的维修策划组负责泵组、压缩机、风机三类设备，现有180张历史作业。每天从维修记录导出 `远航_维保用件_20261115.xlsx`，人工划线找共同用件，再把经验写进指引。三个痛点是重复备件把次数放大、只看一起出现而忽略总体使用率、方案缺少来源证据。上游为实际维保作业；本项目替代集合整理、关联挖掘和兼容性审阅；下游为组长编写作业指引。本项目不管理库存、采购或销售订单。

四模块是作业清单（录入/修订）、阈值配置（分层建组/异步挖掘/封存）、规则解释（发现集指标+留验验收）、备件包（来源规则/来源批次/说明/签核）。实体 Job、MiningRun、AssociationRule、Kit 都真实持久化；规则按批次单独入表，备件包通过rule_id关联并保留source_run_id来源批次。

## 独立留验：发现集与留验集

同批作业既找规则又验效会把偶然联用当成可用方案，因此建组即分层：

- **建组分层**：按设备（泵组/压缩机/风机）分层，层内按作业稳定排序后等距抽取留验集（默认30%，可调5%–50%），划分是确定性的，相同输入永远得到同一归属。快照每条作业带 `cohort=discovery/holdout` 标记，两集不相交，留验集至少2张、发现集至少2张。
- **跨修订归属不变**：建组时冻结整组快照与依据 checksum；之后作业修订只影响新建的组，已建组的分层归属和快照不变。
- **封存前留验不参与筛选**：Apriori 仅在发现集上跑，留验用件在封存前不进入任何阈值筛选或计数。
- **封存**：挖掘完成后封存规则与同一版依据（发现集/留验集/整组三份 checksum），封存与作业修订并发时仍引用同一版快照，依据对不上则拒绝封存；封存幂等。
- **留验验收**：封存后只评估既定规则，按留验作业重算每条规则的共同次数及 support/confidence/lift，沿用建组时的原阈值，逐单给出 `passed`（达标）、`failed`（未达标）、`insufficient_sample`（前件在留验集未出现或留验有效作业不足2张）。整体全部达标才置 `accepted`，否则停留 `sealed` 可重试；重复验收不重复发布。
- **备件包门槛**：仅 `accepted` 批次中留验结论为 passed 的规则可新建备件包；旧包保留，通过 source_run_id 可追溯来源批次。

|角色|页面权限|典型动作|
|---|---|---|
|admin|全部，独占审计和签核|签核适用范围|
|analyst|业务读写、挖掘、导出|配置阈值并起草备件包|
|viewer|全部业务只读、导出|核对来源规则；后端禁止写入/计算|

## 快速启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 [localhost:18207](http://localhost:18207)，后端 [localhost:19207/docs](http://localhost:19207/docs)，MySQL端口17707。账号为 `admin@demo.local`、`analyst@demo.local`、`viewer@demo.local`，密码均 `Demo123!`。`SEED_DEMO=true` 自动执行种子；手动再跑自然键幂等，不覆盖人工修订。初始3账号、180作业、12类标准备件，无预制计算结果。

## 一天的工作剧本

1. 08:50 viewer 打开YH-1115-001至180，核对泵组作业背景。
2. 09:00 analyst 查看目录：轴承、密封圈、润滑脂、滤芯、垫片、联轴器、皮带、紧固件、叶轮、阀片、传感器、清洗剂。
3. 09:10 录入 `YH-1115-181`，泵组，用件“轴承,润滑脂,密封圈”。
4. 09:20 检查重复用件会去重，不因领了两份润滑脂而计两次事务。
5. 09:30 建立“11月泵组联用留验复核”，支持度0.15、置信度0.6、提升度1，留验比例30%；系统按设备分层划出发现集与留验集并冻结快照。
6. 09:40 worker完成后刷新，进入规则解释，核对发现集共同作业数和分母（仅发现集）。
7. 09:50 点击封存：规则与同一版依据冻结，即使此刻有人修订作业，本批次依据不变。
8. 10:00 执行留验验收：逐单查看留验集重算的共同次数与三项指标，发现集高分而留验未达标的规则被拦下，不能建包。
9. 10:10 对留验通过的一条规则点击起草备件包，写“仅限同规格泵组，核对密封材质和数量”；旧备件包保留来源批次。
10. 10:20 admin签核；viewer下载 `parts-affinity-<run-id>.json`，内含发现集/留验集快照、checksum与留验报告。

## 算法口径和失败态

Python标准库实现Apriori，最大频繁项集长度3。每张含备件的有效作业是一个事务，parts转集合后计数，空事务排除并记录excluded；有效事务N至少2。每张作业内的重复件不增加支持度。最小计数ceil(N×min_support)，用1e−12容差避免浮点整数边界。挖掘事务N仅统计发现集作业。

对不相交非空A/B，support=count(A∪B)/N，confidence=count(A∪B)/count(A)，lift=confidence/support(B)。候选由上一层频繁项集连接，先检查所有子集频繁再计数。按提升度、置信度、支持度排序，签名作稳定次序。用户可配置支持度(0,1]、置信度(0,1]、提升度[0,50]、留验比例(0,1)，只接受12项标准目录。阈值太高时成功完成但没有规则，页面提示降低阈值或增加同类作业；不伪造规则。发现集样本不足则任务failed。相同来源作业生成稳定checksum；建组即冻结样本，后续修订不改变已有报告。

留验验收不重新挖掘：封存后从 AssociationRule 取既定规则，在留验集事务上重算 count(A)、count(B)、count(A∪B) 与三项指标，阈值沿用建组值。留验有效作业不足2或规则前件从未出现记 `insufficient_sample`；指标全部达标记 `passed`，任一未达标记 `failed`。状态机为 queued→running→done→sealed→accepted（failed 为挖掘失败终态，验收 failed 不停留失败而保持 sealed 允许重试）。封存/验收接口幂等，重复调用不重复写规则、不重复发布产物；worker 仅在成功路径写 `/artifacts/<run-id>.json`，失败重试不产生残留发布。

关联不是必须联换，提升度也不证明因果。备件包用件来自规则并集，人工只能填写名称、适用性和限制；已签核不可覆盖。没有数量推算、设备兼容性数据库或采购动作。

## 工程与目录

|层|实现|
|---|---|
|前端|原生HTML/CSS/ES modules，Nginx|
|后端|Python3.11、FastAPI、SQLAlchemy、PyJWT|
|后台|Redis队列，独立worker执行Apriori，文件心跳|
|数据库|MySQL8.0，Redis7|

恰好frontend/backend/worker/db/redis五服务，全部healthcheck。backend等db/Redis健康，worker额外等backend，frontend等backend。数据库、Redis与计算产物各用命名卷；worker结果在共享`/artifacts`。Nginx `/api/` 转发到后端根路由；Redis和worker不映射宿主端口。`docker compose down -v --remove-orphans` 清理本项目数据。

`backend/src`分routes/controllers/services/models/schemas/middlewares/config/utils/constants；`frontend/src`分api/stores/pages/components/common/hooks/utils/router/constants/types。共用表格、字段、状态标签，表单hooks与HTML转义；请求ID和安全响应头中间件，业务写入审计。首次启动ORM建表，migrations保存实际DDL，不含版本迁移执行器。

本地开发：先起db/redis，backend安装requirements，配置DATABASE_URL/REDIS_URL/JWT_SECRET后 `uvicorn src.main:app --reload`；前端使用Nginx同源代理或本地同源开发代理。环境变量为COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET，见.env.example。当前显式子网10.253.108.0/24用于规避本机默认池耗尽，迁移可调整。演示凭据与JWT密钥部署时需替换。

枚举ROLES/STATES/EQUIPMENT/PARTS分别在后端`src/constants/enums.py`、输入schema及前端`src/constants/enums.js`；实体列见models/entities.py。留验逻辑测试 `python3 -P scripts/test_holdout.py`（22项）与API集成测试 `python3 -P scripts/test_api_holdout.py`（38项，含发现集高分而留验失败、修订竞争、失败重试不重复发布），SQLite+FakeRedis即可运行。测试与截图在`../../test-artifacts/gb-3108/`。

已知范围：逐条表单/API录入，无CSV导入/删除/分页；规则单worker计算，最大项集3件；分层为按设备等距轮询的确定性划分，不做随机K折交叉验证；签核在站内完成，无采购、外部通知或自动作业指令。Google Fonts离线时回退字体；未做大规模压测。许可证MIT，见LICENSE。
