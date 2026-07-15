"""initial schema: all 8 schemas (iam/agent/skills/knowledge/data/trace/harness/evolution)

Single consolidated migration for the prototype phase. Creates all tables,
indexes, constraints, partitions, and HNSW vector indexes in FK dependency
order. Downgrade drops everything via CASCADE.

Revision ID: 0001
Revises:
Create Date: 2026-06-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extensions ---
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ============================================================
    # 1. IAM Schema — 身份与权限
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS iam')

    op.execute(
        """
        CREATE TABLE iam.tenants (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(200) NOT NULL,
            slug        VARCHAR(50) NOT NULL UNIQUE,
            status      VARCHAR(20) NOT NULL DEFAULT 'active',
            settings    JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.departments (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name        VARCHAR(200) NOT NULL,
            parent_id   UUID REFERENCES iam.departments(id),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name)
        )
        """
    )
    op.execute('CREATE INDEX idx_departments_tenant ON iam.departments(tenant_id)')

    op.execute(
        """
        CREATE TABLE iam.users (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            email       VARCHAR(255) NOT NULL,
            name        VARCHAR(200) NOT NULL,
            role        VARCHAR(30) NOT NULL DEFAULT 'employee',
            status      VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, email)
        )
        """
    )
    op.execute('CREATE INDEX idx_users_tenant ON iam.users(tenant_id)')

    op.execute(
        """
        CREATE TABLE iam.memberships (
            user_id       UUID NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
            department_id UUID NOT NULL REFERENCES iam.departments(id) ON DELETE CASCADE,
            role          VARCHAR(20) NOT NULL DEFAULT 'member',
            joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(user_id, department_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.permissions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            role        VARCHAR(30) NOT NULL,
            resource    VARCHAR(50) NOT NULL,
            action      VARCHAR(30) NOT NULL,
            "constraint"  JSONB,
            UNIQUE(tenant_id, role, resource, action)
        )
        """
    )

    # ============================================================
    # 2. Agent Schema — Agent 分发
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS agent')

    op.execute(
        """
        CREATE TABLE agent.agents (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            scope         VARCHAR(20) NOT NULL,
            owner_id      UUID,
            name          VARCHAR(200) NOT NULL,
            description   TEXT,
            model_config  JSONB NOT NULL DEFAULT '{}',
            capability    JSONB NOT NULL DEFAULT '{}',
            status        VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_agent_scope CHECK (scope IN ('personal', 'department', 'company')),
            CONSTRAINT chk_agent_owner CHECK (
                (scope = 'personal' AND owner_id IS NOT NULL) OR
                (scope = 'department' AND owner_id IS NOT NULL) OR
                (scope = 'company' AND owner_id IS NULL)
            )
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_agents_tenant_scope ON agent.agents(tenant_id, scope, owner_id)'
    )
    op.execute(
        'CREATE INDEX idx_agents_owner ON agent.agents(owner_id) WHERE owner_id IS NOT NULL'
    )

    op.execute(
        """
        CREATE TABLE agent.sessions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id      UUID NOT NULL REFERENCES agent.agents(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            thread_id     VARCHAR(200) NOT NULL,
            user_id       UUID NOT NULL REFERENCES iam.users(id),
            title         VARCHAR(500),
            status        VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX idx_sessions_agent ON agent.sessions(agent_id, created_at DESC)')
    op.execute('CREATE INDEX idx_sessions_thread ON agent.sessions(thread_id)')
    op.execute('CREATE UNIQUE INDEX uq_sessions_thread ON agent.sessions(thread_id)')

    op.execute(
        """
        CREATE TABLE agent.agent_skills (
            agent_id      UUID NOT NULL REFERENCES agent.agents(id) ON DELETE CASCADE,
            skill_id      UUID NOT NULL,
            enabled       BOOLEAN NOT NULL DEFAULT true,
            assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(agent_id, skill_id)
        )
        """
    )

    # ============================================================
    # 3. Skills Schema — 技能市场
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS skills')

    op.execute(
        """
        CREATE TABLE skills.skills (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            scope         VARCHAR(20) NOT NULL,
            owner_id      UUID,
            name          VARCHAR(100) NOT NULL,
            display_name  VARCHAR(200) NOT NULL,
            description   TEXT NOT NULL,
            category      VARCHAR(50) NOT NULL,
            risk_level    VARCHAR(10) NOT NULL DEFAULT 'low',
            guardrail     JSONB NOT NULL DEFAULT '{}',
            status        VARCHAR(20) NOT NULL DEFAULT 'draft',
            version       VARCHAR(20) NOT NULL DEFAULT '0.1.0',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_skill_scope CHECK (scope IN ('personal', 'department', 'company')),
            CONSTRAINT chk_skill_risk CHECK (risk_level IN ('low', 'medium', 'high')),
            CONSTRAINT chk_skill_status CHECK (status IN ('draft', 'published', 'deprecated')),
            UNIQUE(tenant_id, name)
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_skills_tenant_scope ON skills.skills(tenant_id, scope, owner_id)'
    )
    op.execute(
        'CREATE INDEX idx_skills_category ON skills.skills(tenant_id, category, status)'
    )

    op.execute(
        """
        CREATE TABLE skills.skill_versions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            skill_id      UUID NOT NULL REFERENCES skills.skills(id) ON DELETE CASCADE,
            version       VARCHAR(20) NOT NULL,
            yaml_content  JSONB NOT NULL,
            changelog     TEXT,
            created_by    UUID NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(skill_id, version)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE skills.assignments (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            skill_id      UUID NOT NULL REFERENCES skills.skills(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            user_id       UUID NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
            agent_id      UUID REFERENCES agent.agents(id) ON DELETE CASCADE,
            assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(skill_id, user_id, agent_id)
        )
        """
    )
    op.execute('CREATE INDEX idx_assignments_user ON skills.assignments(user_id)')
    op.execute('CREATE INDEX idx_assignments_skill ON skills.assignments(skill_id)')

    # ============================================================
    # 4. Knowledge Schema — 知识引擎
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS knowledge')

    op.execute(
        """
        CREATE TABLE knowledge.ontologies (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name          VARCHAR(200) NOT NULL,
            version       VARCHAR(20) NOT NULL DEFAULT '1.0.0',
            status        VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE knowledge.ontology_nodes (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ontology_id   UUID NOT NULL REFERENCES knowledge.ontologies(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            node_type     VARCHAR(30) NOT NULL,
            name          VARCHAR(200) NOT NULL,
            parent_id     UUID REFERENCES knowledge.ontology_nodes(id),
            properties    JSONB NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_ontology_nodes_ontology ON knowledge.ontology_nodes(ontology_id, node_type)'
    )
    op.execute(
        'CREATE INDEX idx_ontology_nodes_tenant ON knowledge.ontology_nodes(tenant_id)'
    )

    op.execute(
        """
        CREATE TABLE knowledge.documents (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            source_type   VARCHAR(30) NOT NULL,
            source_uri    TEXT NOT NULL,
            title         VARCHAR(500) NOT NULL,
            content_hash  VARCHAR(64) NOT NULL,
            version       INTEGER NOT NULL DEFAULT 1,
            metadata      JSONB NOT NULL DEFAULT '{}',
            status        VARCHAR(20) NOT NULL DEFAULT 'indexed',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, content_hash, version)
        )
        """
    )
    op.execute('CREATE INDEX idx_documents_tenant ON knowledge.documents(tenant_id, status)')

    op.execute(
        """
        CREATE TABLE knowledge.chunks (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id   UUID NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            chunk_index   INTEGER NOT NULL,
            content       TEXT NOT NULL,
            token_count   INTEGER NOT NULL,
            embedding     vector(1024),
            metadata      JSONB NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX idx_chunks_document ON knowledge.chunks(document_id, chunk_index)')
    op.execute('CREATE INDEX idx_chunks_tenant ON knowledge.chunks(tenant_id)')
    op.execute(
        """
        CREATE INDEX idx_chunks_embedding ON knowledge.chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )

    op.execute(
        """
        CREATE TABLE knowledge.org_memories (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            scope         VARCHAR(20) NOT NULL,
            owner_id      UUID,
            memory_type   VARCHAR(30) NOT NULL,
            content       TEXT NOT NULL,
            embedding     vector(1024),
            confidence    REAL NOT NULL DEFAULT 0.5,
            source        VARCHAR(30) NOT NULL DEFAULT 'agent',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_accessed TIMESTAMPTZ,
            access_count  INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_memories_tenant_scope ON knowledge.org_memories(tenant_id, scope, owner_id)'
    )
    op.execute(
        """
        CREATE INDEX idx_memories_embedding ON knowledge.org_memories
            USING hnsw (embedding vector_cosine_ops)
        """
    )

    # ============================================================
    # 5. Data Schema — 数据接入
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS data')

    op.execute(
        """
        CREATE TABLE data.datasources (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name          VARCHAR(200) NOT NULL,
            source_type   VARCHAR(30) NOT NULL,
            connection    JSONB NOT NULL,
            schema_mapping JSONB NOT NULL DEFAULT '{}',
            access_mode   VARCHAR(10) NOT NULL DEFAULT 'read',
            status        VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE data.query_history (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            datasource_id UUID NOT NULL REFERENCES data.datasources(id),
            user_id       UUID NOT NULL,
            natural_query TEXT NOT NULL,
            generated_sql TEXT NOT NULL,
            executed      BOOLEAN NOT NULL DEFAULT false,
            success       BOOLEAN,
            result_count  INTEGER,
            error_message TEXT,
            latency_ms    INTEGER,
            feedback      VARCHAR(20),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_query_history_tenant ON data.query_history(tenant_id, created_at DESC)'
    )
    op.execute(
        'CREATE INDEX idx_query_history_feedback ON data.query_history(tenant_id, feedback) WHERE feedback IS NOT NULL'
    )

    # ============================================================
    # 6. Trace Schema — 观测层（HASH 分区表）
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS trace')

    op.execute(
        """
        CREATE TABLE trace.spans (
            id            UUID NOT NULL,
            tenant_id     UUID NOT NULL,
            trace_id      UUID NOT NULL,
            parent_span_id UUID,
            agent_id      UUID NOT NULL,
            session_id    UUID,
            granularity   VARCHAR(20) NOT NULL,
            name          VARCHAR(200) NOT NULL,
            start_time    TIMESTAMPTZ NOT NULL,
            end_time      TIMESTAMPTZ,
            duration_ms   INTEGER,
            status        VARCHAR(20) NOT NULL,
            attributes    JSONB NOT NULL DEFAULT '{}',
            events        JSONB NOT NULL DEFAULT '[]',
            cost_tokens   INTEGER,
            cost_usd      NUMERIC(10,6),
            user_id       UUID,
            PRIMARY KEY(id, tenant_id)
        ) PARTITION BY HASH (tenant_id)
        """
    )

    # 8 个 HASH 分区
    for i in range(8):
        op.execute(
            f'CREATE TABLE trace.spans_p{i} PARTITION OF trace.spans '
            f'FOR VALUES WITH (modulus 8, remainder {i})'
        )

    op.execute('CREATE INDEX idx_spans_trace ON trace.spans(trace_id)')
    op.execute(
        'CREATE INDEX idx_spans_agent_time ON trace.spans(agent_id, start_time DESC)'
    )
    op.execute(
        'CREATE INDEX idx_spans_tenant_granularity ON trace.spans(tenant_id, granularity, start_time DESC)'
    )
    op.execute(
        'CREATE INDEX idx_spans_status ON trace.spans(tenant_id, status, start_time DESC) WHERE status != \'ok\''
    )

    # ============================================================
    # 7. Harness Schema — 治理层
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS harness')

    op.execute(
        """
        CREATE TABLE harness.quotas (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            scope         VARCHAR(20) NOT NULL,
            owner_id      UUID,
            period        VARCHAR(10) NOT NULL,
            token_limit   BIGINT NOT NULL,
            token_used    BIGINT NOT NULL DEFAULT 0,
            cost_limit_usd NUMERIC(10,2),
            cost_used_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
            reset_at      TIMESTAMPTZ NOT NULL,
            UNIQUE(tenant_id, scope, owner_id, period)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE harness.audit_logs (
            id            BIGSERIAL PRIMARY KEY,
            tenant_id     UUID NOT NULL,
            actor_type    VARCHAR(20) NOT NULL,
            actor_id      UUID NOT NULL,
            action        VARCHAR(50) NOT NULL,
            resource_type VARCHAR(50) NOT NULL,
            resource_id   UUID,
            detail        JSONB NOT NULL DEFAULT '{}',
            ip_address    INET,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_audit_tenant_time ON harness.audit_logs(tenant_id, created_at DESC)'
    )
    op.execute(
        'CREATE INDEX idx_audit_actor ON harness.audit_logs(tenant_id, actor_type, actor_id)'
    )

    op.execute(
        """
        CREATE TABLE harness.quality_metrics (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            skill_id      UUID NOT NULL,
            metric_date   DATE NOT NULL,
            call_count    INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            adoption_rate REAL,
            avg_latency_ms INTEGER,
            UNIQUE(tenant_id, skill_id, metric_date)
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_quality_skill ON harness.quality_metrics(tenant_id, skill_id, metric_date DESC)'
    )

    op.execute(
        """
        CREATE TABLE harness.evolution_strategies (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            training_run_id UUID NOT NULL,
            stage         VARCHAR(30) NOT NULL,
            stage_status  VARCHAR(20) NOT NULL,
            stage_detail  JSONB NOT NULL DEFAULT '{}',
            promoted_at   TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_evolution_tenant ON harness.evolution_strategies(tenant_id, stage, stage_status)'
    )

    # ============================================================
    # 8. Evolution Schema — 进化闭环
    # ============================================================
    op.execute('CREATE SCHEMA IF NOT EXISTS evolution')

    op.execute(
        """
        CREATE TABLE evolution.feedback_signals (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            trace_id      UUID NOT NULL,
            span_id       UUID NOT NULL,
            user_id       UUID NOT NULL,
            agent_id      UUID NOT NULL,
            signal_type   VARCHAR(30) NOT NULL,
            signal_value  VARCHAR(10) NOT NULL,
            strength      REAL NOT NULL,
            captured_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_feedback_tenant_time ON evolution.feedback_signals(tenant_id, captured_at DESC)'
    )
    op.execute('CREATE INDEX idx_feedback_trace ON evolution.feedback_signals(trace_id)')

    op.execute(
        """
        CREATE TABLE evolution.datasets (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            name          VARCHAR(200) NOT NULL,
            pair_count    INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE evolution.preference_pairs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dataset_id    UUID NOT NULL REFERENCES evolution.datasets(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            prompt        TEXT NOT NULL,
            chosen        TEXT NOT NULL,
            rejected      TEXT NOT NULL,
            source_trace_id UUID,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX idx_pairs_dataset ON evolution.preference_pairs(dataset_id)')

    op.execute(
        """
        CREATE TABLE evolution.training_runs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            dataset_id    UUID NOT NULL REFERENCES evolution.datasets(id),
            base_model    VARCHAR(100) NOT NULL,
            method        VARCHAR(20) NOT NULL DEFAULT 'dpo',
            status        VARCHAR(20) NOT NULL,
            metrics       JSONB NOT NULL DEFAULT '{}',
            model_artifact_path TEXT,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at  TIMESTAMPTZ
        )
        """
    )


def downgrade() -> None:
    op.execute('DROP SCHEMA IF EXISTS evolution CASCADE')
    op.execute('DROP SCHEMA IF EXISTS harness CASCADE')
    op.execute('DROP SCHEMA IF EXISTS trace CASCADE')
    op.execute('DROP SCHEMA IF EXISTS data CASCADE')
    op.execute('DROP SCHEMA IF EXISTS knowledge CASCADE')
    op.execute('DROP SCHEMA IF EXISTS skills CASCADE')
    op.execute('DROP SCHEMA IF EXISTS agent CASCADE')
    op.execute('DROP SCHEMA IF EXISTS iam CASCADE')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
    op.execute('DROP EXTENSION IF EXISTS vector')
