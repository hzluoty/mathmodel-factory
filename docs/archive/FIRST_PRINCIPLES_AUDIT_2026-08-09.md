# Paper Factory 第一性原理审计
> **历史快照（2026-08-09）**：本文记录当时的代码、运行态与生产观察，不能作为当前工作流、部署或安全状态的现役说明。当前入口以 [`README.md`](../../README.md)、[`DOCUMENTATION_INDEX.md`](../../DOCUMENTATION_INDEX.md)、[`STEPS.md`](../../STEPS.md)、[`modeling_guide.md`](../../modeling_guide.md) 和 [`web/docs/deployment/DEPLOYMENT.md`](../../web/docs/deployment/DEPLOYMENT.md) 为准。
>
> 研究时间：2026-08-09 UTC | 所属领域：自治数学建模、可验证研究流水线、Web 控制平面 | 研究对象类型：软件系统与运行体系

## 摘要

Paper Factory 已经不是一个简单的「让大模型写论文」脚本。它有 17 步工作流、SQLite 状态机、分阶段审计、求解器收据、三角色评委、Web ACL、Cloud Solver 隔离开关和 770 项通过的锁定测试。单看代码规模和合同密度，它已经跨过了原型期。

但从第一性原理看，系统仍未证明最重要的命题：**给定一份真实题目，它能在受控权限、受控预算和可重放证据下，稳定地产出一份由现行合同认可、可安全发布的正确论文。**

当前证据与这个命题之间有四处断裂：

- 现行合同下没有任何 `CURRENT_PASS` 项目；唯一 native_v2 在 Step 16 失败。
- Web、Agent 与本地 Solver 没有真正的权限分舱，生成代码可能继承 Web 所加载的全部 Secrets 与仓库权限。
- 生产域名由一个脱离 systemd 的遗留进程维持，正式服务已重启失败超过 1.5 万次；HTTP 200 因而是一个假绿色信号。
- 评委、selector 与「独立证据」有大量实现，却没有当前 exact-runtime 的冻结盲测、人类校准和 clean-room 重放证据。

我的总体判断是：**Paper Factory 是一套合同先进、证据落后一拍的研究工厂。** 当前最缺的不是更多提示词或更多代理，而是把权限、证据、授权和发布四个边界变成无法由被审对象自行绕过的系统事实。

报告按只读审计边界执行：没有修改业务代码、真实项目数据库、生产服务、Cloud Run 配置、Secrets、既有工作树或日志；主动新增内容只有本报告及其 HTML/PDF 版。锁定测试暴露了一个自身问题：它在被 gitignore 的 `run_state/solver_jobs/` 生成了 33 个测试 job 文件。本轮保留这些现场证据，没有擅自清理。

---

## 一、一句话定义与审计标尺

### 1.1 一句话定义

Paper Factory 应被定义为：

> 在固定竞赛时限内，把题目与附件转化为可复算的模型、受约束的求解记录、结论—证据映射和正式交付包，并让每一步都可由独立机制拒绝，而不是依赖生成者自述正确。

这个定义故意没有把「论文写得像论文」放在中心。论文只是末端呈现。上游数值错了、证据不可重放、执行环境可越权、评委没有校准，排版再漂亮也不构成可信交付。

### 1.2 从目标反推的八条必要条件

系统要宣称可生产使用，至少同时满足八条不变量：

1. **输入边界**：题目、附件、网页内容和用户上传均被视为不可信数据，不能借 prompt injection 取得执行权限。
2. **执行边界**：浏览、模型 Agent、本地 Solver、Cloud Solver、Web 认证各用最小身份、最小环境和最小文件权限。
3. **计算真值**：关键数字来自可执行 oracle、约束残差、上下界、交叉算法或独立复算；语言模型只解释证据，不创造证据。
4. **证据身份**：输入 hash、代码 commit、依赖环境、solver 版本、seed、资源与网络策略、输出 hash、PDF hash 能连成一条机器可验证链。
5. **独立审核**：审核器不能被项目内代码、项目内 comparator 或项目内普通 JSON 授权文件控制。
6. **预算终止**：74 小时是调度器执行的全局 deadline，而不只是文档目标；超预算时系统进入预先声明的保底交付路径。
7. **原子发布**：PDF、zip、审计收据与状态要么作为同一个版本完整出现，要么都不出现。
8. **可恢复运行**：线上 listener 属于受管服务，数据库有备份恢复，部署能识别目标 commit，告警能发现重启循环和状态漂移。

任何一条断裂，都会降低系统可做的声明。比如 `pytest` 全绿只能证明被编码的行为符合测试，不能证明线上进程由 systemd 托管，也不能证明一个未被测试的恶意题面读不到环境变量。

### 1.3 严重度口径

| 级别 | 本报告含义 |
|---|---|
| Blocker | 在当前或计划中的真实使用边界内，会破坏安全、交付真实性或最终授权；未解决前不应扩大到不可信输入或宣称生产就绪。 |
| Major | 不一定立即造成事故，但会让质量、复现、部署或运行状态产生系统性假阳性。 |
| Minor | 不直接推翻核心可信度，却会让验证口径、维护和下一次交付更容易漂移。 |
| Nit | 纯表达或低风险整洁问题；本轮没有单列 Nit。 |

---

## 二、纵向分析：系统是怎样走到今天的

### 2.1 第一阶段：从生成流水线到工件流水线

系统早期路径可以从保留的 Legacy runner 和历史项目中看出来：Step 0 到 Step 16 依次完成理解题目、提出方法、求解、画图、写作、评审和打包。那个阶段的核心问题是「代理有没有产出文件」。`checkpoint.md`、`complete/` 目录、论文 PDF 和若干日志承担了事实证明的角色。

这套设计让系统很快具备端到端形态，却也留下一个长期包袱：**文件存在曾经接近于状态成立。** 后来的合同不断变严，历史项目依然留在 `complete/`，名称和目录语义却没有随合同升级而自然失效。今天 11 个项目都有交付痕迹，但按 `2026-08-04.incremental_audit_v6` 重新审核，全部只能归为 `LEGACY_DELIVERED`。

这个历史决定解释了当前不少表面矛盾。Web 可以展示「完成论文」，审计却给出 `CURRENT_PASS=0`；一个项目可以有 PDF 和 delivery manifest，仍缺当前 final audit、Step 8.5、v4 provenance 或当前 final-judge fingerprint。存储层保留历史是合理的，产品层若不显示认证等级，就会把「曾经完成」和「当前合同认可」压成同一个词。

### 2.2 第二阶段：把状态从文本搬进 SQLite

native_v2 引入 schema v4 的 `.factory/state.db`，把步骤、attempt、revision、事件、worker lease 和 solver job 变成结构化事实。这个转向非常关键：它把恢复、并发控制和状态演进从 shell 约定提升成状态机合同。

分阶段审计也在这一时期变得清晰。model、results、paper profile 分别核查模型、数值和论文；`factory_core.audit` 与 delivery 分离；最终审核绑定 snapshot；override 保留真实 verdict，不伪造 `PASS`。这些方向与可信系统的基本结构是相符的。

代价是系统出现了两个速度不同的世界。合同层迭代很快，现存项目和运行证据跟不上。当前唯一 ongoing 项目 `cumcm_2020_a_codex_luna` 的 SQLite 权威状态为 `last_completed_step=15`、`active_step=16`、`status=failed`，失败事件是 `PERMANENT_DELIVERY_ACCEPTANCE`。它说明状态机确实能拒绝错误交付，也说明整个新链尚未通过一例。

### 2.3 第三阶段：Web 控制平面与多人边界出现

Web 从本地仪表盘演进为带注册、管理员审批、项目 ACL、showcase ACL、上传和运行控制的 FastAPI/Vue 控制平面。`web/auth.db` 以 bcrypt 保存密码，普通用户只能访问授权项目，展示 ACL 与项目控制 ACL 分离。这些都比「共享一个服务器目录」成熟得多。

问题也在这里发生了性质变化。单用户、可信题目条件下，Web 进程把整个环境传给 worker 只是工程便利；多用户、可上传题目条件下，它变成权限升级路径。HTTP ACL 保护了路由，却没有约束被路由启动的 Agent 和 solver。系统的产品边界扩展到了不可信用户，执行边界仍保留单机可信脚本时代的假设。

更具体地说，systemd 在启动 Web 时加载模型密钥、JWT Secret 和管理员密码。`WorkerLauncher` 复制 `os.environ`，模型 backend 再复制一次，本地 Solver 又复制一次。Codex 非 isolated 路径使用 `--dangerously-bypass-approvals-and-sandbox`，Claude 使用 `--dangerously-skip-permissions`。于是，Web ACL 和执行 ACL 不在同一个安全层级。

### 2.4 第四阶段：求解证据、评委拆分和 selector 治理

当前主线已经具备 `solver-job-evidence-v2`、submitted/completed 两阶段收据、输入与输出 hash、seed、执行文件和平台信息。评委也从单一分数拆成 math、execution、paper 三角色，再由 aggregate 和 decision router 汇总。selector 则明确规划了 R0a hard-gate、R0b 人类盲评、R3 shadow cohort 和人工授权。

这是系统当前最有价值的进展：文档没有把未校准分数冒充奖级真值，Cloud Solver 也保持 `SOLVER_EXECUTION_ENABLED=false`。系统已经学会说「不知道」。

然而，实现与放权之间仍隔着真实 campaign。当前 `human_calibration.ready=false`、`score_reliability.ready=false`、`award_prediction_ready=false`；R0a、R0b、R3 都只有实现与本地验证，没有冻结真实 cohort。final evaluator 已是 hard-role-v2、paper-role-v3、aggregate-v3，而 calibration 脚本仍写 `judge-role-v1` 与 packet-v2。若不把 composite runtime identity 作为硬门，未来甚至可能生成一份「ready」但校准错对象的报告。

### 2.5 历史留下的核心矛盾

回看这条演进路径，今天的问题不是偶然散落的 bug，而是三种历史惯性叠加：

- **文件即事实的惯性**：导致 override、showcase 和历史 complete 项目容易获得超出证据的语义。
- **单机可信执行的惯性**：导致 Web、Agent、Solver 共用环境、身份和仓库权限。
- **先实现规则、后补真实样本的惯性**：导致合同与测试越来越强，却没有 `CURRENT_PASS`、盲测或 clean-room replay 作为最终分母。

理解这三点，修复顺序会清晰很多。继续增加 prompt 条款不会修复权限分舱；继续增加 unit test 不会自动产生端到端通过样本；再写一个 JSON 字段也不会让项目内授权成为可信的人类授权。

---

## 三、2026-08-09 当前事实快照

### 3.1 仓库与测试

| 项目 | 当前证据 | 正确解释 |
|---|---|---|
| 主工作树 | `main` 与 `origin/main` 均为 `f71b754`，主树干净 | 主线本身没有未提交业务修改 |
| 附属 worktree | 3 个 worktree 都有用户修改；两个校准/合同 worktree 无主线缺失实现，PR21 分支保留旧 v1 工作 | 不能擅自清理，也不能把旧分支重新合并造成 v2 降级 |
| 锁定全套测试 | `770 passed` | 实现与现有测试一致，不代表运行态、隔离和真实交付已证明 |
| focused tests | workflow/audit/solver/calibration 相关 `214 passed`；Web `23 passed`；Cloud `31 passed` | 对应代码合同稳定，但生产孤儿进程、metadata 隔离、备份恢复等不在覆盖范围 |
| 测试副作用 | `run_state/solver_jobs/` 有 33 个 2026-08-09 新文件；目录共约 5.4 MB | 全绿测试会污染真实 Legacy 运行态，隔离不完整 |

### 3.2 交付与 ongoing 项目

重新执行 `python3 scripts/audit_complete_projects.py --no-write` 得到：

```text
CURRENT_PASS=0
GATE2_OVERRIDE_DELIVERED=0
LEGACY_DELIVERED=11
INVALID_OR_INCOMPLETE=0
```

唯一 ongoing 项目执行 `run_paper.sh --infer-step` 返回 15。SQLite 状态显示 Step 16 失败，Gate 2 当前文本 verdict 是 `REOPEN_REVISION_MODEL`，math role 为 FAIL，paper role 为 INDETERMINATE。项目内虽存在启用的 `gate2_delivery_override.json`，最终 audit 记录和 `decision_route.json` 都没有生成，所以它既不是 `CURRENT_PASS`，也不是成功的 override delivery。

SQLite 还保存了 4 个 `running` solver jobs。四个 PID 都已不存在，退出 JSON 已存在；Web native list 直接展示数据库字段，不调用 `FactoryService.solver_status()` 调和，因此 UI 能长期显示假 RUNNING。

### 3.3 生产 Web

生产域名 `https://tfisher.de/` 返回 HTTP 200，但 8000 端口的 listener 是 PID `1707626`：

```text
python3 -m web.backend.app
started: 2026-08-06 08:44:12 UTC
cgroup: 普通登录 session，而非 paper-factory-api.service
```

正式 systemd unit 在短暂 `active` 与 `activating/auto-restart` 间循环。审计快照记录 `NRestarts=15431`，之后仍会增长；错误日志反复出现 `[Errno 98] address already in use`。`web/deploy.sh` 在 restart 两秒后只看 `systemctl is-active`，随后请求固定端口。遗留 listener 可以让两个检查同时短暂为绿。

前端也发生漂移：

```text
本地 dist/index.html  SHA-256 31425198bddd49d29e8d3be0074c50bb4b5a3bf427bfb0f709946d5629f3e58f
线上 index.html       SHA-256 084f3936b482b5525ef425e769a2a94db314e3e70686fb0a41bf35f288bb053b
本地构建时间          2026-08-08
线上文件时间          2026-08-04
```

因此，GitHub 主线、生产后端入口和生产前端不是同一个可证明版本。

### 3.4 Cloud Solver

Cloud Run 的安全兜底仍有效：

- service：`solver-api`，region：`europe-west4`；
- revision：`solver-api-p1-e6ec2ad`，2026-07-29 创建，100% 流量且 Ready；
- image：不可变 digest `sha256:226624a2372c12c94192a33d520ae93d3c8feae22c92a563a0666b2d12086065`；
- IAM：只有专用 `solver-invoker` service account 拥有 `roles/run.invoker`，没有 `allUsers`；
- execution：`SOLVER_EXECUTION_ENABLED=false`；
- maxScale：10，container concurrency：1。

这证明当前 quarantine 有效，不证明它已可启用。线上 revision 早于主线 P0 hardening，live `/capabilities` 不具备当前代码接口。更关键的是，求解脚本仍作为同一 Cloud Run 容器内的降权 subprocess 运行；没有网络 namespace、egress deny 或 metadata server 阻断。Cloud Run service identity 可以从 metadata server 取得 token，solver-runner 又对 job bucket 有对象管理权限。UID 10001 解决了部分文件权限，不解决云身份边界。

### 3.5 Web 数据与公开展示

`web/auth.db` 当前权限为 600，`PRAGMA integrity_check=ok`，这是正面控制。数据库采用 `journal_mode=delete`，位于应用同机同仓库目录；未找到在线备份、异机恢复或 RPO/RTO 演练。JWT 默认 24 小时，无 `jti` 或 session 版本；logout 只返回成功，不撤销 token；浏览器把 bearer token 存在 `localStorage`。

公开 showcase 当前列出 `cumcm_2025_a`、`cumcm_2025_b`、`test_cumcm2024a_polished`，API collection 统一写为 `CUMCM · 完成论文`。这三个项目都属于 `LEGACY_DELIVERED`，其中列表末项按当前合同只 infer 到 Step 13。展示可见性 ACL 本身没有越权，但展示语义没有暴露认证等级。

---

## 四、需要解决的问题：按严重度排序

## Blocker

### B1. Web、Agent 与本地 Solver 共用高权限环境和文件域

**必要条件**：处理不可信题目或多用户请求的执行体，不能继承认证 Secrets、管理员凭据、其他项目和整个仓库的读写权。

**当前证据**：systemd unit 先加载 GCP Secret Manager；`factory_core/service.py:72-92` 用 `**os.environ` 启动 worker；`factory_core/adapters/models/backends.py:47-54` 再把环境传给模型后端；Codex/Claude 使用跳过沙箱/权限参数；`factory_core/adapters/solvers/local.py:27-56` 又把完整环境传给求解 worker。systemd 的 `ReadWritePaths` 是整个 `/home/tfisher/paper_factory`。

**风险**：恶意附件、题面 prompt injection 或生成脚本可以尝试读取 `/proc/*/environ`、`web/auth.db`、其他项目和 Secrets，甚至修改仓库。此审计证明攻击路径存在，不代表已经发生泄露。

**可证伪验收**：

- 浏览、Agent、Solver、Web 分成独立身份和进程/容器平面；
- 环境使用显式 allowlist，worker 永远拿不到 JWT、管理员密码和无关模型密钥；
- 项目级文件系统只读输入、单一可写输出，无法读取其他项目与 `auth.db`；
- Solver 默认断网、禁止云 metadata、限制 CPU/内存/PID/磁盘/输出；
- 用恶意题面做真实端到端对抗，读取环境、跨项目访问、仓库写入和网络外连全部失败。

### B2. 生产 Web 的受管服务已经失效，部署验收会假通过

**必要条件**：生产 HTTP 响应必须由目标 unit、目标入口和目标版本提供，而不是「端口上恰好有人响应」。

**当前证据**：8000 listener 属于 8 月 6 日启动的 `python3 -m web.backend.app`，不在正式 unit cgroup。`paper-factory-api.service` 因端口占用持续重启，审计时重启计数超过 15400。`web/deploy.sh:138-188` 只做短时 active 与 HTTP 200 检查，无法验证 listener 所有权。

**风险**：部署、backend-only 更新或回滚可能显示成功，实际代码没有切换。遗留进程退出或主机重启后，服务可能直接中断；systemd 无法可靠恢复当前 listener。

**可证伪验收**：

- 8000 只有一个 listener，PID 属于 `paper-factory-api.service` cgroup；
- `NRestarts` 在稳定观察窗口内不增长，杀死 MainPID 后 unit 恢复唯一实例；
- health 返回 build ID、commit、入口和 schema identity；
- deploy 验证 listener PID/cgroup、稳定窗口、目标 build、前端指纹和重启计数；
- 遗留 listener 存在时，部署必须失败而不是复用其 200；
- 完整部署、backend-only、主机重启与回滚各完成一次真实演练。

这项修复涉及停止当前遗留进程和重启生产服务，需要单独运维授权。本轮没有执行。

### B3. 没有任何项目证明当前合同可以端到端完成

**必要条件**：一个生产流水线至少要有一个代表性样本在当前代码、当前合同和当前执行环境下从输入走到原子交付。

**当前证据**：`CURRENT_PASS=0`；11 个 complete 项目全部是 legacy；唯一 native_v2 失败在 Step 16。770 个测试通过与零个当前交付通过同时成立。

**风险**：每个组件都可能局部正确，组合路径仍可能卡在 fingerprint、final audit、收据、packaging 或状态迁移。没有真实样本，就无法估计无条件交付率、失败分母和修复成本。

**可证伪验收**：冻结一个代表性题目，从 Step 0 新建运行到 Step 16；final audit 为当前 snapshot `PASS`，所有关键 solver receipt 为 v2 且 `receipt_ready=true`，PDF/zip/manifest/state 同版；再在 clean-room 环境重放一次，数值落入预注册容差，随后由 `audit_complete_projects.py` 归类为 `CURRENT_PASS`。

对当前 `cumcm_2020_a_codex_luna` 应优先做故障隔离和针对性修复，不应无证据地从 Step 0 重跑。建立「全新基准」与修复「现有最小差距项目」是两个实验目的，不能混成一次运行。

### B4. Step 14/15 改稿后，Final Audit 没有重跑完整确定性论文检查

**必要条件**：最终发布版本必须重新通过所有可能被后处理破坏的确定性门，而不是只证明评委看过某个 hash。

**当前证据**：paper profile 在 `factory_core/audit/incremental.py:366-420` 执行 numbers、symbols、deliverables、derived artifacts、invariants 等检查。Step 14 写摘要、Step 15 改全文。Final Audit 的 `_run_acceptance_checks()` 在 `factory_core/audit/service.py:445-485` 只重跑 provenance 和 quality contract，没有重新执行 paper profile。

**风险**：摘要中的数字、表格、附件或派生图表在 Step 14/15 被改坏后，只要未校准 LLM 没有发现，仍可能获得交付许可。snapshot hash 证明输入没有在评审后变化，不证明这个输入本身通过确定性数值合同。

**可证伪验收**：让 paper profile 先通过，再分别篡改摘要数字、表格、xlsx、派生图和页数；Final Audit 必须以对应机器检查失败。修复并重新运行后才允许 `PASS`。最好让 final audit 直接消费同一 snapshot 的 paper-profile receipt，而不是复制两套检查列表。

### B5. Gate 2 override 位于被审项目的普通可写域

**必要条件**：人工授权必须来自被审对象无法自行写入的治理平面。

**当前证据**：`factory_core/steps/specialized.py:591-602` 和 `scripts/workflow_state.py:47-63` 只检查项目内 `gate2_delivery_override.json` 的三个普通字段：`enabled=true`、scope 正确、reason 非空。Agent 与项目代码拥有该目录写权限。Final Audit 可以把非 PASS 记录为 `OVERRIDDEN` 且 `delivery_allowed=true`。

**风险**：被治理 Agent 可以创建或篡改授权文件，越过最终 judge。当前实现正确保留 verdict、没有伪造 `PASS`，但「不伪造质量结论」不等于「授权来源可信」。

**可证伪验收**：项目内自行写入 override 必须无效；授权保存在受 ACL 保护的控制平面或 append-only 事件库，绑定 actor、项目、snapshot、reason、scope、有效期和签名/版本；需要双向测试：合法授权可继续，Agent 侧伪造和过期授权都被拒绝。

### B6. Cloud Solver 解除 quarantine 前缺少网络与云身份隔离

**必要条件**：不可信 solver 即使取得容器内代码执行，也不能访问 metadata、外网、其他 job 或云身份。

**当前证据**：主线已经有非 root UID、资源限制、只读输入、输出限制和进程组清理，但 `cloud/solver_runner.py:312-397` 仍是同一 Cloud Run 容器中的 subprocess。API 控制进程保持 root；没有 network namespace 或 metadata deny。线上 runtime service account 对 Solver bucket 有对象管理权。

**风险**：若未来只把 `SOLVER_EXECUTION_ENABLED` 改为 true，恶意脚本可能通过 metadata token 越过「当前 job 输出目录」的应用层边界。

**可证伪验收**：真实云端恶意任务无法访问 metadata、外网、其他 job 对象或 service identity；逃离进程组后仍拿不到权限。推荐把不可信执行移到 Cloud Run Job、Batch 或等价的每任务隔离单元，使用每 job 最小身份与对象前缀授权。在上述证据成立前保持 quarantine。

## Major

### M1. 评委与 selector 没有当前 runtime 的有效性证据

现有报告均显示 `score_reliability.ready=false`、`human_calibration.ready=false`、`award_prediction_ready=false`。R0a/R0b/R3 没有真实 held-out campaign。更麻烦的是，运行时 evaluator 已是 hard-role-v2、paper-role-v3、aggregate-v3，`scripts/evaluate_calibration.py:13-17` 仍指向 role-v1 与 packet-v2。

风险不是「分数可能有点抖」，而是目前无法知道 false accept 和 false reject 分别有多高。多跑几次取最高分会放大正向评分误差，也就是 winner's curse。

验收标准应包含完整 composite identity：hard role、paper role、aggregate、packet builder、prompt、模型、backend 和输入指纹。旧身份报告必须 fail closed。之后按顺序完成客观 mutation 的 R0a、人类盲评 R0b、隔离 shadow cohort R3；三者通过后才签发有限 scope、有效期和 canary 的人工授权。

### M2. 旧 Step 3 自动排序仍是可被措辞操纵的启发式

`web/backend/selection_service.py:98-170` 用 demo status 赋固定分，coverage 依赖关键词计数，innovation 因出现 `robust/鲁棒` 加分，risk 按 `MAJOR/风险/warning` 出现次数计算，然后自动排序。

在新 selector 尚未获准时，这套旧逻辑仍可能选择错误主线。验收口径应是：R0b 未 ready 前，只做 hard-feasibility 淘汰；剩余候选输出 `TIE/NEEDS_HUMAN`，CLI 与 Web 都保留人工选择。校准和有限授权后再允许自动质量排序。

### M3. Step 2 收据要求仍主要停留在 prompt，正式状态采用失败黑名单

critic prompt 要求 v2 receipt 与 hash 绑定，但 `factory_core/steps/validators.py:213-215` 的 Step 2 validator 只确认至少两个 stream 有 demo/spec 且 critic 首行是 `VALIDATED`，没有机器解析 receipt。canonical audit 的 `INCOMPLETE_STATUSES` 只列 `RUNNING/PARTIAL/PENDING/INCOMPLETE/FAILED`；未知状态、空状态或 `ERROR` 不在失败集合。

验收应改为成功白名单：validator 直接加载每个 demo 的 v2 receipt，核对 job id、script、inputs、seed、outputs、exit 和 event binding；`receipt_ready=false`、未知状态、错误输出或脚本不匹配全部失败。

### M4. 「独立证据」仍可由项目内自述和项目内 comparator 形成

quality contract 已能阻止证据完全缺失，却未完全证明独立性。bound proof 主要检查 locator 存在；cross-check 检查两个不同 `family` 字符串和非空 conclusion；dual implementation 检查两个文件 hash 不同，再执行项目内 comparator。项目 Agent 仍能写一个永远成功的 comparator，或给同一算法换两个 family 名称。

验收需要 factory-owned oracle 和 hash-pinned comparator；每个算法运行各自绑定 solver receipt、输入、seed 和输出；故意构造虚假宽界、伪造 family、always-pass comparator 的 mutation suite 必须被拒绝。再加一轮独立 clean-room replay，绑定依赖 lock、solver library、commit/container digest、资源和网络策略。

### M5. 视觉硬缺陷默认处于 shadow，页数限制没有进入 native final audit

`JUDGE_POLICY_MODE` 未设置时默认 `shadow`。`scripts/judge_decision_router.py:227-230` 会保留旧 LLM decision，即便新的视觉路线要求 reopen。`pdf_visual_gate.py` 支持 `--max-pages`，Final Audit 调用没有传它。

验收要求 release 路径固定 enforce；缺字体、越界、空白页和超页 PDF 必须阻断 Step 16。shadow 只能运行在隔离实验目录，不能向 `papers/` 或 `complete/` 发布。

### M6. 74 小时只是目标，没有成为全流程调度约束

`STEPS.md` 写明 74 小时目标。native catalog 的单次 step timeout 合计约 53 小时；乘以各步 `max_attempts` 后理论上约 242 小时，还不含部分角色重试、reopen 和人为等待。状态域虽然有 `deadline_epoch`，当前主要服务于人类选择 gate，没有全流程剩余预算调度、模型 token/cost 台账或保底交付状态机。

验收应在创建项目时固定 global deadline、货币/token/solver 预算；每次调度依据剩余预算决定模型、并行度和求解 ladder；预算不足时转入预注册的 canonical 保底路径。最终报告全部尝试、失败率、超时率、成本曲线、Best Attempt 与最终提交差距，不只展示最好一次。

### M7. 发布不是原子事务，submission zip 也不由题目合同驱动

`factory_core/steps/specialized.py:815-831` 先把 PDF replace 到 `papers/`，之后才生成 zip；packaging 失败会留下新 PDF 与缺失/旧 zip 的半交付。`scripts/package_submission.py:65-75` 用静态目录集合打包，包括 `data/raw`、`problem`、`results`，没有按 `problem/deliverables.json` 和赛事提交约束生成成员集。

验收应把 PDF、zip、manifest、audit receipt 都放入同一 staging version，完整验证后一次 promote；失败时正式目标保持上一完整版本或全部不存在。zip 成员严格来自题目 deliverables contract，并验证无多余文件和敏感输入。

### M8. showcase 把历史交付与当前质量认证混成一个产品语义

线上三个公开项目都不是 `CURRENT_PASS`，API 却统一标记 `CUMCM · 完成论文`。这不是 ACL 缺陷，而是认证信息缺失。

验收可以选择两条路线：正式展示只允许 `CURRENT_PASS`；或保留历史样本，但 API/UI 明确显示 `HISTORICAL / LEGACY_DELIVERED / CURRENT_PASS`、合同版本和限制说明。绝不能用目录位置推断认证等级。

### M9. Solver Jobs UI 会保留假 RUNNING，测试还会写真实运行目录

`web/backend/solver_jobs_api.py:34-73` 直接读取 SQLite；`FactoryService.solver_status()` 才会调和死 PID 和 exit file。当前四个 job 已证明列表可长期漂移。

同时，路由测试调用根 `solver_submit.sh`，Legacy job 根硬编码为仓库 `run_state/solver_jobs`。审计当天新增 33 个被 gitignore 的 job 文件。测试全绿却改变真实运行态。

验收应让 list、detail、CLI 和 Web 共用同一个 reconcile 服务，并持久化 terminal event；测试通过临时 `FACTORY_ROOT`/job root 完全隔离，测试结束后真实 `run_state` 的文件数和 hash 不变。

### M10. 上传合同存在越权引用、内存放大和压缩炸弹路径

普通用户创建项目申请时可以提交任意「服务器上存在的路径」，没有绑定本人上传对象。上传先 `await file.read()` 全量进入内存，再检查 100 MB。archive extraction 只防 traversal 与 tar link，没有总展开字节、文件数、目录深度和压缩比限制。生产 nginx 未配置 `client_max_body_size`，实测约 2 MiB 已返回 413，与应用 100 MB 口径冲突。

验收应让上传返回不可猜的 opaque ID，记录 owner、digest、size、状态与保留期；申请只能引用本人未消费对象。网关与应用统一大小合同，服务端流式限额；解压限制成员数、展开总量、深度和压缩比；zip bomb、并发上传、用户累计配额和清理策略都有负向测试。

### M11. 认证防滥用、会话撤销和浏览器防护未闭环

Login/Register schema 没有长度约束；登录同步执行 bcrypt，没有账号/IP rate limit、失败审计或并发上限。JWT 只有 `sub/role/status/exp`，logout 不撤销 token，前端用 `localStorage` 保存 bearer token。线上响应未观察到 HSTS、CSP、frame-ancestors 和 Referrer-Policy。

验收应覆盖账号/IP 双维度限速、失败告警、bcrypt 受限线程池或独立服务、明确密码字节长度、可撤销 session/refresh token、logout 后旧凭据立即失效，以及 HttpOnly/Secure/SameSite cookie 或有充分测试的等价方案。CSP/HSTS 与 XSS/session theft 测试需要进入部署验收。

### M12. 认证与 ACL 是未演练恢复的单机状态，审计记录不完整

`auth.db` 当前完整且权限正确，但仍是同机单文件 SQLite、DELETE journal。未找到在线一致备份、异地恢复和损坏恢复演练。audit API 先读取全部记录再截 200；登录成败、项目 pause/resume/terminate、咨询回答、Step 3 决策和模型配置修改没有统一写入持久审计；审计与业务数据还在同一个可写数据库。

验收需要明确 RPO/RTO，完成在线一致备份、异机恢复、损坏恢复和并发写测试；所有权限、配置与运行 mutation 记录 actor、目标、前后 revision、结果和 correlation ID；审计支持分页、保留策略、完整性校验与异地导出。

### M13. Cloud Solver 生产版本、状态模型和监控都没有跟上主线

线上 revision 早于当前 hardening，live capabilities 也不是当前代码。`cloud/solver_api.py:184-186` 使用实例内字典；GCS manifest 持久化异常在 `save()` 中只记录日志后仍返回；`/jobs` 只列当前实例内存。live maxScale=10，实例重启和横向扩展会造成列表不一致。没有持续 health heartbeat、合成故障或告警策略；本地 health 快照停在 7 月 29 日。

验收应采用外部事务状态库/队列、原子建单和状态 CAS；持久化失败必须拒绝调度；跨 10 实例、重启、重试和重复 job id 测试保持一致。监控要通过合成故障证明告警能送达。解决 B6 之前仍不启用执行，部署新版也不能替代隔离验收。

### M14. Git 主线、生产后端和生产前端缺少统一版本身份

当前前端 hash 已漂移，后端由兼容入口遗留进程托管，Cloud 又是旧 revision。生产响应没有给出一个能同时绑定 Git commit、frontend build、backend schema 与部署时间的身份。

验收应为每次 release 生成不可变 manifest，绑定 commit、lock hash、backend build、frontend hash、Cloud revision/digest 和数据库 schema；health 与管理员 ops 页面只展示非敏感身份元数据。发布验收必须拿该 manifest 与实际 listener、静态文件和 Cloud revision 对账。

## Minor

### m1. 默认测试入口不自洽，缺少真实离线 E2E 层

正确全套命令需要 `--extra web --extra cloud --extra models --group dev`。普通 `uv run --isolated --locked pytest -q` 会因缺 bcrypt、FastAPI、requests、numpy 等在 collection 阶段报错。新开发者很容易把依赖矩阵错误当产品回归，或把 unit suite 成功当端到端证明。

应提供单一根测试命令或 `test` extra，并增加真实 SQLite、真实 local solver receipt、真实 TeX/visual gate、stub judge 的离线 E2E；再保留一个受控 live-model smoke，明确它不进入普通 CI。

### m2. 保留 worktree 没有主线缺失实现，却存在误合并降级风险

三个 worktree 都有未提交状态。校准与合同 worktree 的实现已在 main，主要差异是旧文档；PR21 保留旧 solver-evidence-v1 提交，而 main 已有更严格 v2。清理需要用户明确授权，当前不应删除；后续应先生成唯一 patch/hash 清单，确认无独有实现，再归档或清理。任何合并都要证明不会从 v2 降回 v1。

### m3. 日志与运行命名空间缺少生命周期治理

`api.error.log` 已约 7.7 MB，反复重启仍持续增长，未找到 logrotate。`ongoing/` 除真实项目外还有 `data/` 与 `results/` 顶层目录，infer 为 -1；被忽略的 runtime 文件又不在 Git 状态中显现。

应对日志设轮换、大小上限和保留期；项目发现只接受满足身份合同的目录；管理员 ops 页面显示 runtime residue 数量与年龄。删除现有日志、目录或测试残留仍需另行确认。

---

## 五、横向分析：与可信自治研究系统的同期基线对照

Paper Factory 没有完全同形的直接竞品。OpenAI deep research 偏向联网研究报告；Google AI Co-Scientist 和 Sakana AI Scientist-v2 偏向科研假设与论文生成；SWE-bench Verified 和 MLGym 则更接近「如何证明自治 Agent 真做对了」。把它们放在同一个截面上，价值不在功能列表，而在信任机制。

| 参考系统 | 已有机制 | 机制不能证明的事情 | 对 Paper Factory 的直接启示 |
|---|---|---|---|
| OpenAI deep research | 发布前安全评测、外部 red team、风险分级、上线后监控；Python 执行环境无网络 | 系统卡不证明报告每条引文都支持对应结论，也不提供完整可重放运行包 | 浏览与计算分平面；联网内容视为不可信；安全上线、质量评估与发布治理分开 |
| Google AI Co-Scientist | 多代理生成、反思、排序、演化；内部 Elo 趋势；小规模盲评；专家选择并参与实验验证 | Elo 是系统自评，不是独立真值；端到端实验仍有人在环 | tournament 只能生成候选，不能替代 oracle、人类盲评和发布授权 |
| Sakana AI Scientist-v2 | 开放代码；自动实验与写作；公开少量人类评审和 AI 披露 | 人类从生成结果中选了 3 篇，成功样本很小；被选中样本不能代表全尝试分母 | 所有候选、失败和筛选过程都要计入成功率，公开样本需披露 AI 与限制 |
| SWE-bench Verified | 任务绑定 repo、base commit、test patch、环境 commit；Docker 执行；逐实例日志与结果 | 通过测试不等于实现完整、可读或安全；固定公开集也会污染 | 数学建模需要题目 snapshot、可执行 oracle、人工确认可解集和独立 blind set |
| MLGym | 非 root 容器、只读数据与 evaluator；时间/步数/成本限制；多次独立运行；trajectory | 反复看到 test score 会过拟合；`latest` 镜像不是不可变身份 | 报告全部尝试、Best Attempt 与 Best Submission；开发验证与最终盲测隔离 |

### 5.1 Paper Factory 已经领先于普通 Agent 脚本的地方

Paper Factory 的优势不是模型能力，而是它已经形成治理语言：

- 状态机有 revision、事件和 lease，不只看文件；
- audit 与 delivery 分离，override 不伪造 PASS；
- solver receipt 已进入 v2，两阶段绑定输入、seed 和输出；
- final judge 拆分 hard role 与 paper role，分数标为未校准诊断；
- Cloud Solver 默认 quarantine，IAM 已从公开调用收紧到专用 invoker；
- selector 计划明确要求 R0a、R0b、R3 和人工授权，没有直接把 `max(K)` 接到生产。

这些机制让系统具备继续建设的骨架。许多类似项目会在「多 Agent 能互相批评」时宣布完成，Paper Factory 已经知道互相批评不等于客观真值。

### 5.2 与可信基线相比仍缺的共同底座

五个参考方向尽管产品形态不同，却共同指向六件事：

1. **客观 oracle 优先于语言评分**。约束残差、上下界、独立计算和测试补丁是硬证据；自然语言评委负责解释和筛查遗漏。
2. **全尝试分母**。成功样本、被选中的最好论文和最高分都不能替代无条件完成率、失败率、超时率与预算曲线。
3. **执行隔离**。非 root 只是起点，网络、metadata、文件系统、凭据、资源和输出均需边界。
4. **冻结盲测**。开发集、公开历史题和最终评测必须分离，防止污染与 selector 过拟合。
5. **claim-level provenance**。最终论文的关键数字、图表和结论要定位到结构化结果字段、生成脚本和 job receipt。
6. **审核与发布分权**。PRECHECK、质量 PASS、人工例外和实际发布是不同状态，不应由项目内文件合并成一个信号。

Paper Factory 在第 6 点已有正确方向，在第 3、4、5 点仍缺真实运行证据。它当前最接近「SWE-bench 式合同框架已经搭好，但 Verified 数据集和隔离执行还没完成」。

### 5.3 不能照搬的做法

横向参照也有陷阱。Co-Scientist 的 Elo 不能直接移植成论文质量真值；AI Scientist-v2 的最好一篇不能成为稳定成功率；SWE-bench 的 pass/fail 不评价论文叙事；MLGym 的可见 test score 会诱发对测试集调参。

Paper Factory 应保留自己的分层结构：数学/执行 hard gates 提供可证伪底线，人类校准的 paper role 判断表达与说服力，selector 只在对应证据范围内获得权限。一个总分不应重新把这些边界压扁。

---

## 六、横纵交汇：真正的系统性判断

### 6.1 控制平面已经跑在执行平面前面

Web 有用户、ACL、审批和展示，表面上是多人系统；执行层仍是假设「同一台机器上的调用者都可信」。历史上这是合理捷径：流水线先要跑起来，完整环境继承最省事。今天同一个捷径把 JWT、管理员密码、模型密钥和不可信生成代码放进同一进程树。

因此，下一阶段不能再把「加一个 API 权限检查」当安全修复。真正的边界必须落在 OS/container/cloud identity 上。HTTP 层拒绝 Alice 看 Bob 的项目，并不能阻止 Alice 的 solver 读取 Bob 的目录。

### 6.2 规则数量增长快于证据数量

系统已经有大量 verifier、schema、manifest 和 focused test。可验证规则越多，`CURRENT_PASS=0` 越显眼。这不是规则失败，而是一种研发阶段信号：合同已经进入「需要真实 campaign 反向打磨」的阶段。

若继续只加规则，项目会不断被新的合同追赶，历史样本永远停在 legacy；若为了得到一个 PASS 而放宽规则，又会失去前期建设的价值。正确动作是冻结一个合同窗口，选定代表样本，记录每次真实阻断，修复合同中的假阳性与漏检，再做 clean-room replay。一个真实基准带来的信息量，会高于再增加几十个字符串断言。

### 6.3 机构事实不能住在项目可写目录

override、评委授权、发布收据和认证等级属于机构事实：它们表达的是「谁批准了什么」。项目工件表达的是「生成者提交了什么」。两者放在同一可写目录，逻辑上就让被审对象兼任审批者。

这条判断也适用于 project-owned comparator、自报 family、自报 solver 版本。项目可以提供证据候选，却不能定义验证自己的最终规则。factory-owned oracle、控制平面授权和 append-only event 是把角色重新分开的方式。

### 6.4 当前最强资产是“会拒绝”，不是“会高分”

唯一 native_v2 最终失败、Cloud 保持 quarantine、校准 flag 保持 false，这些看起来像进度不佳，却是健康信号。系统至少没有把明显缺证据的状态包装成成功。

真正危险的是其他假绿色：遗留进程提供的 HTTP 200、`systemctl is-active` 的短暂窗口、Web 的 RUNNING 行、历史项目的「完成论文」标签、项目内 override 的「用户授权」语义。下一轮工程应围绕消灭这些假绿色，而不是追求更多绿色数量。

### 6.5 三个未来剧本

#### 最可能剧本：高质量的内部研究工具

如果维持 Cloud quarantine、限制可信操作员、修复 final audit 与生产托管，Paper Factory 可以较快成为强内部工具。人类负责题目准入、Step 3 和最终授权，系统负责生成、证据采集和拒绝明显错误。这个剧本不要求 selector 立即自动化，也不要求开放任意用户。

观察指标：出现第一个 `CURRENT_PASS`；连续多次运行的无条件完成率可计算；生产 listener 受 systemd 管理；项目内伪造 override 无效。

#### 最危险剧本：把控制平面的成熟外观误当成安全隔离

若继续开放上传和运行控制，同时保留完整环境继承与无沙箱执行，一次恶意题面就可能把模型幻觉升级为权限事故。另一个风险是部署脚本持续报告成功，实际生产长时间停在旧代码，直到孤儿进程退出才暴露。

预警指标：Cloud 被简单改为 enabled；新增匿名或普通用户运行权限；`NRestarts` 继续增长；health 没有 build identity；showcase 继续把 legacy 当正式认证。

#### 最乐观剧本：可验证的自治建模工厂

如果权限分舱、factory-owned oracle、claim graph、冻结盲测、全局预算和原子发布全部落地，Paper Factory 会形成少见的完整体系：生成器可以快速探索，solver 在隔离环境里给出不可变收据，hard gates 用客观证据拒绝，paper judge 只在校准范围内判断表达，人类授权来自独立控制平面，最终 release 可以在干净环境重放。

这个剧本的门槛不是模型再强一点，而是系统能回答每个关键问题：谁运行、用什么环境、读写了什么、花了多少、哪个 job 产生这个数字、哪个人批准这个 snapshot、线上到底在服务哪个版本。

---

## 七、建议的修复顺序与验收路线

### 7.1 P0：立即收敛假绿色与暴露面

| 动作 | 完成定义 |
|---|---|
| 单独批准并修复生产 orphan listener | 唯一 listener 属于 unit；重启计数稳定；部署能识别遗留进程并失败 |
| 保持 Cloud quarantine | `SOLVER_EXECUTION_ENABLED=false` 持续绑定 live revision，直到 B6 对抗测试通过 |
| 限制不可信用户触发 Agent/Solver | 在权限分舱完成前，把运行能力限定为受信操作员或明确的可信题目 |
| 把 showcase 标为 legacy | 三个公开项目显示真实认证等级和合同限制 |
| 冻结 Secrets 操作 | 没有证据表明已泄露；先修边界，再根据暴露评估单独批准轮换 |

### 7.2 P1：修复三条核心信任边界

1. Web、Agent、Local Solver 拆身份、环境和文件系统；加入真实恶意题面对抗测试。
2. Final Audit 绑定并重跑 post-polish 的完整 paper profile；visual gate 在 release 强制 enforce。
3. override 移出项目目录，进入 ACL 保护、snapshot-bound、可审计的控制平面。
4. Step 2/5 收据、canonical status 和 quality oracle 改成机器成功白名单与 factory-owned verifier。
5. PDF、zip、receipt、manifest 改成同一 staging transaction。

### 7.3 P2：跑出第一个可重放 CURRENT_PASS

选一个题目作为冻结基准，提前注册：输入 hash、合同版本、模型路由、预算、solver、seed、预期附件、页数、客观 oracle 和容差。记录所有 attempts，不做事后挑样。首次通过后，在干净环境再跑一次，不允许复用项目内缓存作为唯一证据。

这一阶段的目标不是冲高分，而是测出真实漏点：哪一步最常失败、哪些 gate 误拒绝、预算消耗分布、replay 是否一致。只有基线成立，质量优化才有稳定地基。

### 7.4 P3：校准 evaluator 与 selector

按 exact composite runtime 完成 R0a、R0b、R3。R0a 验证 hard gate 对客观 mutation 的敏感度；R0b 用冻结 PDF 和独立人类标签验证自然稿件排序；R3 只在 shadow cohort 里比较 selector 选择与 canonical/随机策略。授权 receipt 限 scope、模型、输入域、有效期和 canary 比例。

### 7.5 P4：Cloud 与多人生产化

Cloud 改用每任务隔离和最小身份；外部事务状态、CAS、监控和告警完成后再启用。Web 补上传 ownership/配额、会话撤销、限速、安全头、auth DB 备份恢复和不可变审计。每次 production release 输出统一 manifest，并做回滚演练。

### 7.6 最小验收计分板

| 维度 | 当前值 | 进入生产化下一阶段的门槛 |
|---|---:|---|
| CURRENT_PASS | 0 | ≥1 个冻结基准，且 clean-room replay 通过 |
| 生产 listener 所有权 | orphan | 唯一 PID 属于 systemd unit，稳定窗口无重启 |
| 执行 Secret 隔离 | 否 | 恶意题面无法读环境、跨项目、外网/metadata |
| Final Audit post-polish 复验 | 不完整 | numbers/deliverables/derived/visual/page 全部 snapshot-bound |
| Override 信任域 | 项目可写文件 | 控制平面授权，绑定 actor/snapshot/expiry |
| Judge human calibration | false | 当前 exact runtime 的冻结盲评 ready |
| Selector authorization | false | R0a+R0b+R3 ready，人工限域 receipt |
| Cloud execution | false | 保持 false，直到隔离、事务状态与告警同时通过 |
| Auth DB 恢复 | 未演练 | 明确 RPO/RTO，异机恢复与损坏恢复成功 |
| Release identity | 漂移 | commit/frontend/backend/cloud/db schema 同一 manifest |

---

## 八、审计边界、证据与信息来源

### 8.1 本地权威证据

访问与核验时间均为 2026-08-09 UTC。

- `AGENTS.md`、`STEPS.md`、`modeling_guide.md`、`CLAUDE.md`。
- `web/README.md`、`web/docs/deployment/DEPLOYMENT.md`。
- `factory_core/steps/catalog.py`：step timeout 与 max attempts。
- `factory_core/audit/incremental.py`、`factory_core/audit/service.py`：分阶段与最终审计。
- `factory_core/steps/specialized.py`、`scripts/workflow_state.py`：override 与交付顺序。
- `factory_core/service.py`、`factory_core/adapters/models/backends.py`、`factory_core/adapters/solvers/local.py`：环境与执行链。
- `web/backend/project_api.py`、`upload_service.py`、`auth.py`、`auth_store.py`、`solver_jobs_api.py`。
- `cloud/solver_api.py`、`cloud/solver_runner.py`、`cloud/cloudbuild.yaml`。
- `evaluation/README.md`、`evaluation/SELECTOR_ROLLOUT_PLAN.md` 与当前 calibration JSON。
- `ongoing/cumcm_2020_a_codex_luna/.factory/state.db`、judge outputs、override 文件与 solver job exit 记录。
- `web/auth.db` 的非敏感 ACL、完整性与 journal metadata；未读取或输出密码 hash、JWT、管理员密码或任何 Secret 值。

主要只读命令：

```text
git status --short --branch
git worktree list --porcelain
python3 scripts/audit_complete_projects.py --no-write
bash run_paper.sh --infer-step ongoing/cumcm_2020_a_codex_luna
python3 -m factory_core.cli state ongoing/cumcm_2020_a_codex_luna
uv run --isolated --locked --extra web --extra cloud --extra models --group dev pytest -q
systemctl show/status paper-factory-api.service
ss -ltnp 'sport = :8000'
sha256sum web/frontend/dist/index.html /var/www/tfisher.de/index.html
gcloud run services/revisions describe ...
gcloud run services get-iam-policy ...
```

### 8.2 外部一手来源

访问时间均为 2026-08-09 UTC。

- OpenAI, *Deep Research System Card*: https://cdn.openai.com/deep-research-system-card.pdf
- Google Research, *Accelerating scientific breakthroughs with an AI co-scientist*: https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/
- Google AI Co-Scientist paper: https://arxiv.org/abs/2502.18864
- Sakana AI, *The AI Scientist-v2*: https://arxiv.org/abs/2504.08066
- Sakana AI official publication experiment: https://sakana.ai/ai-scientist-first-publication/
- SWE-bench paper: https://arxiv.org/abs/2310.06770
- SWE-bench containerized evaluation guide: https://github.com/SWE-bench/SWE-bench/blob/cd37836ffec01d01a0d699a80a039d84ff2cebfe/docs/guides/evaluation.md
- MLGym paper: https://arxiv.org/abs/2502.14499
- MLGym fixed code snapshot: https://github.com/facebookresearch/MLGym/tree/9d40c1b5035202018cd7091fb4e83a9c68b377c0
- Google Cloud Run service identity and metadata server documentation: https://docs.cloud.google.com/run/docs/securing/service-identity

### 8.3 限制说明

- 本轮没有停止 PID `1707626`、重启 systemd、部署前后端或修改 nginx；生产修复仍待单独授权。
- 本轮没有启用 Cloud Solver、提交云端 solver job、修改 IAM 或访问 Secret 值。
- 本轮没有清理三个 worktree、测试残留、日志、`ongoing/data`、`ongoing/results` 或任何生成论文。
- `770 passed` 是本轮锁定环境的当前证据；它不包含真实恶意题面、Cloud metadata 攻击、备份恢复、生产回滚和真实模型端到端 campaign。

### 8.4 方法论说明

本报告采用横纵分析法：纵轴追踪 Paper Factory 从文件流水线、SQLite 状态机到证据与控制平面的演进；横轴用同期自治研究与 Agent 评测系统比较可信机制；交汇处再从历史决策解释当前问题与未来路径。所有严重度判断均以「能否被独立证伪」为核心，而不是按代码量、测试数量或界面完整度推断成熟度。
