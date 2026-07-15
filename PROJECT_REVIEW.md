# Enterprise Agent OS — 项目全景回顾

> **一句话定位**：企业级 AI Agent 治理平台 —— 让 AI Agent 像员工一样被分配、协作、记忆、成长，同时在平台层面被治理和审计。

---

## 一、宏观愿景

### 核心理念

传统 AI 应用是「单点工具」——一个 ChatBot、一个 RAG 系统、一个工作流引擎。企业真正需要的是「Agent 员工」：能被分配到部门、有岗位职责（Skill）、能协作完成复杂任务、能积累组织记忆、能从反馈中进化，且全过程被治理和审计。

EAOS（Enterprise Agent OS）就是这个理念的平台化实现：

```
Act（执行）→ Observe（观测）→ Learn（学习）→ 治理（Harness）→ 更聪明的 Act
```

### 平台价值

| 维度 | 传统 AI 工具 | EAOS |
|------|-------------|------|
| **分发** | 每人独立用 ChatGPT | Agent 按部门/角色分配，技能共享 |
| **协作** | 单轮对话 | 多 Agent 协作（接力/并行/辩论/层级） |
| **记忆** | 无持久记忆 | 三级记忆（个人/部门/公司），自动晋升 |
| **知识** | 临时上传文档 | RAG + 本体 + 记忆引擎统一检索 |
| **安全** | 无管控 | 六维治理（权限/能力/成本/合规/质量/进化） |
| **进化** | 静态模型 | DPO 强化学习 + 影子流量 + 灰度发布 |
| **集成** | 无 | MCP 连接器 + ERP/CRM + IM 网关 |
| **审计** | 无 | 四粒度追踪 + 不可篡改审计日志 |

---

## 二、七层架构 + Harness 治理

```
┌─────────────────────────────────────────────────────────┐
│                    Harness (治理层)                       │
│  权限 · 能力 · 成本 · 合规 · 质量 · 进化                  │
├─────────────────────────────────────────────────────────┤
│  L7  Evolution      反馈采集 → DPO训练 → 影子流量 → 灰度   │
├─────────────────────────────────────────────────────────┤
│  L6  Observability   四粒度追踪（Trace/Span/Audit/Metric） │
├─────────────────────────────────────────────────────────┤
│  L5  Gateway         FastAPI · IM网关 · 多模态 · SSO      │
├─────────────────────────────────────────────────────────┤
│  L4  Agent           LangGraph编排 · 多租户 · 四种协作模式 │
├─────────────────────────────────────────────────────────┤
│  L3  Skills          技能市场 · 九类三档 · 三种执行模式    │
├─────────────────────────────────────────────────────────┤
│  L2  Knowledge       本体 · RAG · 组织记忆引擎            │
├─────────────────────────────────────────────────────────┤
│  L1  Data            MCP连接器 · Text2SQL · ERP/CRM集成   │
├─────────────────────────────────────────────────────────┤
│  L0  Core/Infra      共享内核 · DB · Redis · LLM路由      │
└─────────────────────────────────────────────────────────┘
```

### 治理六柱（Harness — 核心差异化）

这是 EAOS 区别于所有「AI 套壳」产品的核心。每次 Agent 执行动作，都经过六维治理：

| 柱 | 时机 | 职责 |
|----|------|------|
| **Permission** | 动作前 | RBAC 权限检查，用户/Agent 是否有权操作该资源 |
| **Capability** | 动作前 | Agent 能力边界检查，是否超出声明的能力范围 |
| **Cost** | 动作前 | 配额检查，Token/费用/调用次数是否超限 |
| **Compliance** | 动作后 | PII 脱敏、合规审查、审计日志写入 |
| **Quality** | 动作后 | 输出质量评分，低质量结果标记 |
| **Evolution** | 动作后 | 进化治理，模型更新需六步审批（训练→护栏→影子→审批→灰度→全量） |

---

## 三、十大后端模块

### 3.1 Core（L0 — 共享内核）

**职责**：全平台共享的基础类型和工具。

- `TenantContext` — 多租户上下文，贯穿所有请求
- `Principal` — 身份主体（user/agent），含 tenant_id、role、permissions
- 事件总线（`EventBus`）— 模块间松耦合通信
- 统一错误体系（`NotFoundError`、`PermissionDeniedError`等）
- 配置管理（Pydantic Settings，支持环境变量覆盖）

### 3.2 Infra（L0 — 基础设施适配）

**职责**：屏蔽所有外部基础设施差异。

| 子系统 | 实现 |
|--------|------|
| **数据库** | PostgreSQL + asyncpg + SQLAlchemy，16个迁移版本 |
| **向量存储** | pgvector，嵌入向量存储与相似检索 |
| **缓存** | Redis 7，会话/限流/分布式锁 |
| **LLM 路由** | 统一接口，支持 OpenAI / Anthropic / GLM（智谱），按 task_type 路由 |
| **对象存储** | 本地存储（可扩展 S3） |
| **可观测性** | OpenTelemetry → Jaeger + Prometheus + Grafana |

### 3.3 Data（L1 — 数据连接层）

**职责**：连接外部系统，让 Agent 能读写企业数据。

- **MCP 连接器**：标准 MCP 协议（stdio + HTTP），连接任意外部系统
- **ERP 连接器**：订单/库存/客户等 ERP 资源的读写
- **CRM 连接器**：线索/商机/联系人等 CRM 资源的读写
- **HTTP 连接器**：通用 HTTP API 集成，支持 OpenAPI Spec
- **Text2SQL 引擎**：自然语言 → SQL，含 SQL 校验器和沙箱执行
- **连接管理器**：统一注册/发现/健康检查，支持加密凭证存储
- **WritePipeline**：写操作治理管道（权限→HITL审批→执行→审计→回滚→合规）

### 3.4 Knowledge（L2 — 知识引擎）

**职责**：统一知识检索，Agent 只需调一个接口。

**KnowledgeEngine** 是统一门面，内部编排：

1. **本体（Ontology）**：企业知识图谱，定义实体/关系/属性
   - 查询重写：用本体扩展用户查询，提升召回
   - 例如：用户问「客户满意度」→ 自动关联「NPS」「投诉率」「续约率」
2. **RAG 管道**：文档分块 → 嵌入 → 向量检索 → 重排序
   - 支持 PDF/Word/Markdown 等多格式
   - 三级可见性（个人/部门/公司）
3. **记忆存储（Memory Store）**：Agent 对话记忆
   - 三级范围：personal / department / company
   - 记忆晋升：个人记忆可被管理员晋升为部门/公司级
   - 记忆巩固：定期合并去重，保留高价值记忆

### 3.5 Skills（L3 — 技能市场）

**职责**：Agent 的「岗位技能」定义和执行。

**九类三档分类体系**：

| 档位 | 类别 | 说明 | 风险等级 |
|------|------|------|----------|
| **一档（增强）** | knowledge_api | 企业知识查询 | 低 |
| | verification | 业务规则校验 | 低 |
| | data_analysis | 数据查询分析 | 低 |
| **二档（日常）** | process_automation | 流程自动化 | 中 |
| | document_template | 文档模板生成 | 中 |
| | quality_review | 质量审核 | 中 |
| **三档（生产）** | system_operation | 系统操作 | 高（强制护栏） |
| | runbook | 运维手册执行 | 高（强制护栏） |
| | infra_ops | 基础设施运维 | 高（强制护栏） |

**三种执行模式**（按优先级）：
1. `code_execution` — Python 沙箱执行
2. `tool_bindings` — 绑定 MCP 工具，直接调用外部系统
3. `llm` — 纯大模型推理

**生命周期**：draft → published → deprecated（管理员管理）

**三级可见性**：personal（个人）/ department（部门）/ company（全公司）

### 3.6 Agent（L4 — Agent 编排层）

**职责**：多 Agent 协作编排，单 Agent 执行。

**四种协作模式**：

| 模式 | 说明 | 示例 |
|------|------|------|
| **Relay（接力）** | A → B → C 顺序执行 | 销售收集需求 → 技术评估 → 财务报价 |
| **Fan-out/in（扇出/扇入）** | 并行执行 + 汇总 | 多部门并行写年报 → 汇总 |
| **Debate（辩论）** | 多视角 + 裁判 | 技术专家 vs 财务专家 → 裁判决策 |
| **Hierarchical（层级）** | 权限升级委派 | 员工 Agent → 主管 Agent 审批 |

**其他能力**：
- **AgentDispatcher** — Agent 注册/发现/分发
- **AgentRunner** — 单 Agent 执行引擎，流式输出
- **AmbientMonitor** — 主动触发（阈值告警/任务停滞/新事件/定时报告）
- **多租户隔离** — 每个租户的 Agent/技能/数据完全隔离
- **Docker 沙箱** — 代码执行隔离

### 3.7 Gateway（L5 — API 网关）

**职责**：统一 API 入口，连接前端、IM、外部系统。

**28 个路由模块**：

| 类别 | 路由 | 说明 |
|------|------|------|
| **认证** | auth, sso | 登录/JWT/SSO（OIDC/SAML） |
| **用户** | users, roles, departments | 用户/角色/部门 CRUD |
| **Agent** | agents, invoke | Agent 管理 + 对话调用（SSE 流式） |
| **技能** | skills | 技能 CRUD + 发布/废弃 |
| **记忆** | memory | 记忆查询/删除/晋升 |
| **知识** | knowledge, knowledge_docs, ontology | 文档/本体/知识检索 |
| **数据** | connections, data_management, bi | 外部连接/数据管理/BI 查询 |
| **治理** | admin, safety_cases, contributions | 审批/安全案例/贡献度 |
| **监控** | metrics, super_admin | 系统指标/超级管理 |
| **协作** | tasks, sessions, notifications | 任务/会话/通知 |
| **进化** | evolution | 模型训练/影子流量/灰度发布 |
| **集成** | webhook, upload, multimodal_loader | Webhook/文件上传/多模态 |

**IM 网关**：统一消息入口，支持四个渠道：
- 飞书（Feishu）
- 钉钉（DingTalk）
- 企业微信（WeCom）
- Slack

**多模态**：图片/文件处理，多模态消息支持

### 3.8 Observability（L6 — 可观测性）

**职责**：四粒度追踪，全链路可观测。

| 粒度 | 说明 |
|------|------|
| **Trace** | 一次完整对话/任务的全链路 |
| **Span** | 链路中的每一步（LLM 调用/工具执行/检索） |
| **Audit** | 不可篡改审计日志（谁/何时/做了什么/结果） |
| **Metric** | 系统指标（Token 消耗/延迟/成功率） |

集成 Jaeger（分布式追踪）+ Prometheus（指标采集）+ Grafana（可视化看板）

### 3.9 Harness（L7 — 治理层，核心差异化）

**职责**：六维治理，所有 Agent 动作的「守卫」。

**WritePipeline（写操作治理管道）**：

```
用户请求写操作
  → Harness.guard()（权限/能力/成本检查）
  → 高风险？→ HITL 人工审批 → 等待
  → 连接器执行写操作
  → 审计日志记录
  → 失败？→ 自动回滚 + 记录回滚
  → Harness.post_guard()（合规脱敏/质量评分）
  → 返回结果
```

**EvolutionGovernor（进化治理）**：模型更新六步流水线
1. DPO 训练
2. 护栏检查
3. 影子流量验证
4. 人工审批
5. 灰度发布
6. 全量发布（异常自动回滚）

### 3.10 Evolution（进化层）

**职责**：从反馈中学习，持续进化。

**进化闭环**：
```
Act（技能执行）→ Observe（追踪记录）→ Learn（RL训练）→ Harness治理 → 更聪明的Act
```

| 组件 | 职责 |
|------|------|
| FeedbackCollector | 采集用户反馈（点赞/点踩/修正） |
| PreferenceDatasetBuilder | 构建偏好数据集 |
| DPOTrainer | Direct Preference Optimization 训练 |
| GuardrailChecker | 训练后模型护栏检查 |
| ShadowTrafficManager | 影子流量验证（不影响真实用户） |
| ArtifactStore | 训练产物存储 |
| Replay | 经验回放 |

---

## 四、前端架构

### 4.1 管理员端（Admin）— 30+ 功能页面

| 分类 | 页面 | 功能 |
|------|------|------|
| **仪表盘** | dashboard | 系统总览，关键指标 |
| **监控** | monitor-dashboard, monitor-executions, monitor-traces | 实时监控/执行详情/链路追踪 |
| **用户管理** | users, roles, departments, tenants | 用户/角色/部门/租户 CRUD |
| **Agent管理** | agents | Agent 创建/配置/分配 |
| **技能管理** | skills | 技能 CRUD/发布/废弃/绑定工具 |
| **记忆管理** | memory | 记忆查看/删除/晋升/批量操作 |
| **知识管理** | documents, ontology, knowledge | 文档/本体/知识库 |
| **数据管理** | data-management, mcp-connectors | 外部连接/数据源管理 |
| **BI 分析** | bi-data, bi-metrics, bi-query, bi-sql | 数据/指标/查询/SQL |
| **工作流** | workflows, triggers | 工作流设计/触发器配置 |
| **治理** | approvals, audit-logs, safety-cases | 审批/审计/安全案例 |
| **进化** | (evolution API) | 模型训练/影子/灰度 |
| **系统** | settings, sso, models, plugins, notifications | 设置/SSO/模型/插件/通知 |
| **贡献度** | contributions, promotions, report-templates | 贡献/晋升/报告模板 |

### 4.2 员工端（Employee）— 10 个核心页面

| 页面 | 功能 |
|------|------|
| **chat** | 与 Agent 对话（SSE 流式，@技能触发，多模态） |
| **tasks** | 任务列表/分配/完成 |
| **skills** | 浏览可用技能，创建个人技能 |
| **memory** | 查看个人记忆 |
| **knowledge** | 知识库搜索 |
| **bi** | 数据分析报表 |
| **notifications** | 通知中心 |
| **settings** | 个人设置（主题/语言/默认Agent） |

### 4.3 共享组件库（Shared）

| 模块 | 说明 |
|------|------|
| **API Client** | OpenAPI 生成的类型安全 HTTP 客户端 |
| **Auth** | 认证状态管理、路由守卫（AdminRoute/EmployeeRoute） |
| **UI Kit** | Button/Input/Card/Toast/Spinner/Dialog 等组件 |
| **Theme** | 深色/浅色/系统三种主题，CSS 变量驱动 |
| **i18n** | 中英文国际化 |
| **Hooks** | useStreamInvoke（SSE 流式调用）等 |
| **Utils** | cn（类名合并）等工具函数 |

### 4.4 桌面端（Tauri）

| 应用 | 窗口 | 说明 |
|------|------|------|
| **EAOS Admin** | 1440×900 | 管理员桌面客户端 |
| **EAOS Employee** | 1280×800 | 员工桌面客户端 |

特性：CSP 安全策略、自动更新、系统托盘、原生窗口体验

---

## 五、技术栈

### 后端

| 层 | 技术 |
|----|------|
| **语言** | Python 3.12+ |
| **框架** | FastAPI + Uvicorn |
| **数据库** | PostgreSQL 16 + pgvector |
| **缓存** | Redis 7 |
| **ORM** | SQLAlchemy 2.0 + asyncpg |
| **迁移** | Alembic（16个版本） |
| **LLM** | OpenAI / Anthropic / GLM（智谱）统一路由 |
| **追踪** | OpenTelemetry → Jaeger |
| **指标** | Prometheus + Grafana |
| **包管理** | uv (monorepo) |
| **测试** | pytest（全模块测试覆盖） |

### 前端

| 层 | 技术 |
|----|------|
| **框架** | React 19 + TypeScript |
| **构建** | Vite |
| **状态** | TanStack Query（React Query） |
| **路由** | React Router 7 |
| **样式** | Tailwind CSS 4 + CSS 变量 |
| **桌面** | Tauri 2（Rust） |
| **API** | openapi-fetch（类型安全） |
| **i18n** | react-i18next |
| **图标** | Lucide React |

### 基础设施

| 组件 | 说明 |
|------|------|
| **Docker Compose** | PostgreSQL + Redis + Jaeger + OTel + Prometheus + Grafana + API + Worker + Mock-SaaS |
| **K8s** | deploy/k8s/ 部署配置 |
| **CI/CD** | GitHub Actions |

---

## 六、数据库模型（16个迁移版本）

| 迁移 | 内容 |
|------|------|
| 0001 | 初始 Schema（用户/租户/Agent/会话/消息） |
| 0002 | ERP/CRM Mock 表（订单/客户/线索/产品） |
| 0003 | Agent 触发器 |
| 0004 | Harness 策略 + 审批 |
| 0005 | Harness 安全案例 |
| 0006 | 外部连接 |
| 0007 | ERP/CRM 租户隔离 |
| 0008 | 写操作审计日志 |
| 0009 | 技能工具绑定 |
| 0010 | Agent 消息 |
| 0011 | 通知系统 |
| 0012 | 用户偏好配置 |
| 0013 | 知识贡献度 |
| 0014 | 知识可见范围 |
| 0015 | 审批操作详情 |
| 0016 | SSO 配置 |

---

## 七、已完成的功能矩阵

### 核心能力

| 功能 | 状态 | 说明 |
|------|------|------|
| 多租户架构 | ✅ | 租户隔离，每租户独立 Agent/技能/数据 |
| RBAC 权限 | ✅ | admin/super_admin/employee 三级角色 |
| Agent 对话 | ✅ | SSE 流式，多模态，@技能触发 |
| 多 Agent 协作 | ✅ | 接力/并行/辩论/层级四种模式 |
| 技能市场 | ✅ | 九类三档，CRUD/发布/废弃/绑定工具 |
| 记忆引擎 | ✅ | 三级记忆，晋升/删除/批量操作 |
| 知识引擎 | ✅ | RAG + 本体 + 记忆统一检索 |
| MCP 连接器 | ✅ | stdio + HTTP，ERP/CRM/通用 API |
| Text2SQL | ✅ | 自然语言→SQL，沙箱执行 |
| 写操作治理 | ✅ | HITL审批/审计/回滚 |
| IM 网关 | ✅ | 飞书/钉钉/企微/Slack |
| SSO | ✅ | OIDC/SAML |
| 多模态 | ✅ | 图片/文件处理 |
| 主动触发 | ✅ | 阈值/停滞/事件/定时 |
| 四粒度追踪 | ✅ | Trace/Span/Audit/Metric |
| 审计日志 | ✅ | 不可篡改，全操作记录 |
| 进化闭环 | ✅ | 反馈→DPO→护栏→影子→灰度 |
| 深色/浅色主题 | ✅ | 三种模式（深/浅/系统） |
| 国际化 | ✅ | 中英文 |
| 桌面客户端 | ✅ | Tauri 打包，管理员+员工双端 |

### 管理员端功能

- ✅ 仪表盘（系统总览/关键指标）
- ✅ 监控（实时仪表盘/执行详情/链路追踪）
- ✅ 用户/角色/部门/租户管理
- ✅ Agent 创建/配置/分配
- ✅ 技能管理（CRUD/发布/废弃/工具绑定）
- ✅ 记忆管理（查看/删除/晋升/批量删除）
- ✅ 知识管理（文档/本体/知识库）
- ✅ MCP 连接器管理
- ✅ BI 分析（数据/指标/查询/SQL）
- ✅ 审批管理
- ✅ 审计日志
- ✅ 安全案例
- ✅ 通知管理
- ✅ SSO 配置
- ✅ 系统设置
- ✅ 贡献度管理

### 员工端功能

- ✅ Agent 对话（流式/@技能/多模态）
- ✅ 任务管理
- ✅ 技能浏览/创建
- ✅ 个人记忆查看
- ✅ 知识搜索
- ✅ BI 报表
- ✅ 通知中心
- ✅ 个人设置（主题/语言/默认Agent）

---

## 八、部署架构

### 开发环境

```bash
docker compose up -d    # PostgreSQL + Redis + Jaeger + OTel + Prometheus + Grafana + Mock-SaaS
uv run alembic upgrade head  # 数据库迁移
uv run uvicorn eaos_api.main:app --reload  # 后端
pnpm dev               # 前端（admin/employee）
pnpm tauri dev         # 桌面端
```

### 生产部署

```
                    ┌─────────────┐
                    │   Nginx/LB  │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────┴───┐ ┌─────┴────┐ ┌────┴─────┐
     │ API Server │ │  Worker  │ │  Grafana │
     │ (FastAPI)  │ │ (后台任务)│ │ (看板)   │
     └────────┬───┘ └─────┬────┘ └──────────┘
              │            │
     ┌────────┴────────────┴────────┐
     │     PostgreSQL + pgvector    │
     │           Redis 7            │
     │     Jaeger + Prometheus      │
     └──────────────────────────────┘
```

**用户使用方式**：
- **桌面端**：下载安装包 → 双击安装 → 打开 → 登录 → 使用
- **Web 端**：浏览器访问网址 → 登录 → 使用
- **IM 端**：在飞书/钉钉/企微/Slack 中 @Agent → 对话

用户完全不需要知道 Docker、Python、命令行。

---

## 九、项目规模

| 维度 | 数量 |
|------|------|
| 后端模块 | 10 个 Python 包 |
| API 路由 | 28 个路由模块 |
| 前端页面 | 管理员 30+ / 员工 10 |
| 数据库迁移 | 16 个版本 |
| Docker 服务 | 9 个容器 |
| 测试文件 | 60+ 个测试文件 |
| LLM 适配器 | 3 个（OpenAI/Anthropic/GLM） |
| IM 渠道 | 4 个（飞书/钉钉/企微/Slack） |
| 技能类别 | 9 类 3 档 |
| 协作模式 | 4 种 |
| 治理维度 | 6 维 |
| 追踪粒度 | 4 级 |

---

## 十、总结

EAOS 不是一个「ChatBot 套壳」，而是一个**企业级 Agent 操作系统**：

1. **分发层**：Agent 像员工一样被分配到部门，有岗位技能
2. **协作层**：多 Agent 四种协作模式，处理复杂业务流程
3. **知识层**：RAG + 本体 + 记忆三位一体，知识持续积累
4. **集成层**：MCP 连接器 + ERP/CRM + IM 网关，连接一切
5. **治理层**：六维治理 + 写操作管道 + 进化六步，安全可控
6. **进化层**：DPO 强化学习 + 影子流量 + 灰度发布，持续变好
7. **可观测层**：四粒度追踪 + 不可篡改审计，全链路透明

这是一个**平台级产品**，不是单点工具。它的价值在于：让企业能像管理员工一样管理 AI Agent，像治理业务流程一样治理 AI 行为，像积累组织知识一样积累 AI 记忆。
