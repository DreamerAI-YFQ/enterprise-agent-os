"""Demo data seed script for M1/M2 verification.

Inserts a representative dataset across all schemas so that the M1/M2
acceptance flow (``docker compose up`` -> ``alembic upgrade head`` ->
``python -m eaos.infra.db.seed`` -> ``psql SELECT count(*)``) has something
to show.

Idempotent: TRUNCATEs all tables (in FK-reverse order) before inserting. Uses
fixed UUIDs so verification queries can pin specific rows.

M2 additions (over M1):
  - erp schema: products/customers/orders/inventory (10/10/15/10 rows)
  - crm schema: leads/opportunities/activities (10/10/15 rows)
  - knowledge.ontology_nodes: full enterprise ontology (6 Object + 32 Attribute
    + 5 Relation + 3 Rule + 3 Code = 49 nodes) replacing the 2 M1 examples
  - knowledge.chunks: expanded to 6 chunks (3 per document) with optional
    embeddings generated via OpenAIEmbedder when EAOS_EMBEDDING__API_KEY is set

Usage::

    python -m eaos.infra.db.seed
    # or
    uv run python -m eaos.infra.db.seed

Requires ``EAOS_DB__URL`` to point at a migrated database.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from eaos.core.config import AppConfig
from eaos.infra.db.postgres import PgClient
from sqlalchemy.sql import text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

# Fixed UUIDs for verifiable demo data.
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
DEPT_RND = UUID("00000000-0000-0000-0000-000000000101")
DEPT_SALES = UUID("00000000-0000-0000-0000-000000000102")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
USER_MANAGER = UUID("00000000-0000-0000-0000-000000000202")
USER_EMPLOYEE = UUID("00000000-0000-0000-0000-000000000203")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
AGENT_DEPARTMENT = UUID("00000000-0000-0000-0000-000000000302")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")
SKILL_TEXT2SQL = UUID("00000000-0000-0000-0000-000000000401")
SKILL_RAG = UUID("00000000-0000-0000-0000-000000000402")
SKILL_CODE = UUID("00000000-0000-0000-0000-000000000403")
ONTOLOGY_ERP = UUID("00000000-0000-0000-0000-000000000501")
DOC_ERP_MANUAL = UUID("00000000-0000-0000-0000-000000000504")
DOC_CRM_API = UUID("00000000-0000-0000-0000-000000000505")
CHUNK_ERP_1 = UUID("00000000-0000-0000-0000-000000000506")
CHUNK_CRM_1 = UUID("00000000-0000-0000-0000-000000000507")
MEMORY_PREF = UUID("00000000-0000-0000-0000-000000000508")
DS_ERP = UUID("00000000-0000-0000-0000-000000000509")
DS_CRM = UUID("00000000-0000-0000-0000-000000000510")
DS_KNOWLEDGE = UUID("00000000-0000-0000-0000-000000000513")
QUERY_DEMO = UUID("00000000-0000-0000-0000-000000000511")
QUOTA_TENANT = UUID("00000000-0000-0000-0000-000000000512")

# M2 additional chunk UUIDs (3 chunks per document).
CHUNK_ERP_2 = UUID("00000000-0000-0000-0000-000000000513")
CHUNK_ERP_3 = UUID("00000000-0000-0000-0000-000000000514")
CHUNK_CRM_2 = UUID("00000000-0000-0000-0000-000000000515")
CHUNK_CRM_3 = UUID("00000000-0000-0000-0000-000000000516")

# ERP mock data UUIDs (00000000-0000-0000-0000-0000000006XX).
ERP_PRODUCT_IDS = [UUID(f"00000000-0000-0000-0000-0000000006{i:02d}") for i in range(1, 11)]
ERP_CUSTOMER_IDS = [UUID(f"00000000-0000-0000-0000-0000000006{10+i:02d}") for i in range(1, 11)]
ERP_ORDER_IDS = [UUID(f"00000000-0000-0000-0000-00000000062{i:x}") for i in range(1, 16)]
ERP_INVENTORY_IDS = [UUID(f"00000000-0000-0000-0000-0000000006{30+i:02d}") for i in range(1, 11)]

# CRM mock data UUIDs.
CRM_LEAD_IDS = [UUID(f"00000000-0000-0000-0000-0000000006{40+i:02d}") for i in range(1, 11)]
CRM_OPP_IDS = [UUID(f"00000000-0000-0000-0000-0000000006{50+i:02d}") for i in range(1, 11)]
CRM_ACTIVITY_IDS = [UUID(f"00000000-0000-0000-0000-00000000066{i:x}") for i in range(1, 16)]

# Ontology node UUIDs.
NODE_OBJ_CUSTOMER = UUID("00000000-0000-0000-0000-000000000701")
NODE_OBJ_PRODUCT = UUID("00000000-0000-0000-0000-000000000702")
NODE_OBJ_ORDER = UUID("00000000-0000-0000-0000-000000000703")
NODE_OBJ_INVENTORY = UUID("00000000-0000-0000-0000-000000000704")
NODE_OBJ_LEAD = UUID("00000000-0000-0000-0000-000000000705")
NODE_OBJ_OPPORTUNITY = UUID("00000000-0000-0000-0000-000000000706")

# Attribute node UUIDs (0710-072F).
NODE_ATTR_CUSTOMER_NAME = UUID("00000000-0000-0000-0000-000000000710")
NODE_ATTR_CUSTOMER_CODE = UUID("00000000-0000-0000-0000-000000000711")
NODE_ATTR_CUSTOMER_INDUSTRY = UUID("00000000-0000-0000-0000-000000000712")
NODE_ATTR_CUSTOMER_CONTACT = UUID("00000000-0000-0000-0000-000000000713")
NODE_ATTR_CUSTOMER_CREDIT = UUID("00000000-0000-0000-0000-000000000714")
NODE_ATTR_CUSTOMER_CREATED = UUID("00000000-0000-0000-0000-000000000715")

NODE_ATTR_PRODUCT_SKU = UUID("00000000-0000-0000-0000-000000000716")
NODE_ATTR_PRODUCT_NAME = UUID("00000000-0000-0000-0000-000000000717")
NODE_ATTR_PRODUCT_CATEGORY = UUID("00000000-0000-0000-0000-000000000718")
NODE_ATTR_PRODUCT_PRICE = UUID("00000000-0000-0000-0000-000000000719")
NODE_ATTR_PRODUCT_COST = UUID("00000000-0000-0000-0000-00000000071a")
NODE_ATTR_PRODUCT_STATUS = UUID("00000000-0000-0000-0000-00000000071b")

NODE_ATTR_ORDER_NO = UUID("00000000-0000-0000-0000-00000000071c")
NODE_ATTR_ORDER_CUSTOMER_ID = UUID("00000000-0000-0000-0000-00000000071d")
NODE_ATTR_ORDER_PRODUCT_ID = UUID("00000000-0000-0000-0000-00000000071e")
NODE_ATTR_ORDER_QUANTITY = UUID("00000000-0000-0000-0000-00000000071f")
NODE_ATTR_ORDER_AMOUNT = UUID("00000000-0000-0000-0000-000000000720")
NODE_ATTR_ORDER_STATUS = UUID("00000000-0000-0000-0000-000000000721")
NODE_ATTR_ORDER_DATE = UUID("00000000-0000-0000-0000-000000000722")

NODE_ATTR_INVENTORY_PRODUCT_ID = UUID("00000000-0000-0000-0000-000000000723")
NODE_ATTR_INVENTORY_WAREHOUSE = UUID("00000000-0000-0000-0000-000000000724")
NODE_ATTR_INVENTORY_QUANTITY = UUID("00000000-0000-0000-0000-000000000725")
NODE_ATTR_INVENTORY_SAFETY = UUID("00000000-0000-0000-0000-000000000726")

NODE_ATTR_LEAD_NAME = UUID("00000000-0000-0000-0000-000000000727")
NODE_ATTR_LEAD_SOURCE = UUID("00000000-0000-0000-0000-000000000728")
NODE_ATTR_LEAD_STATUS = UUID("00000000-0000-0000-0000-000000000729")
NODE_ATTR_LEAD_CONVERTED = UUID("00000000-0000-0000-0000-00000000072a")

NODE_ATTR_OPP_NAME = UUID("00000000-0000-0000-0000-00000000072b")
NODE_ATTR_OPP_CUSTOMER_ID = UUID("00000000-0000-0000-0000-00000000072c")
NODE_ATTR_OPP_AMOUNT = UUID("00000000-0000-0000-0000-00000000072d")
NODE_ATTR_OPP_STAGE = UUID("00000000-0000-0000-0000-00000000072e")
NODE_ATTR_OPP_CLOSE_DATE = UUID("00000000-0000-0000-0000-00000000072f")

# Relation node UUIDs (0730-0734).
NODE_REL_CUSTOMER_ORDER = UUID("00000000-0000-0000-0000-000000000730")
NODE_REL_ORDER_PRODUCT = UUID("00000000-0000-0000-0000-000000000731")
NODE_REL_PRODUCT_INVENTORY = UUID("00000000-0000-0000-0000-000000000732")
NODE_REL_LEAD_OPPORTUNITY = UUID("00000000-0000-0000-0000-000000000733")
NODE_REL_OPPORTUNITY_CUSTOMER = UUID("00000000-0000-0000-0000-000000000734")

# Rule node UUIDs (0740-0742).
NODE_RULE_LARGE_ORDER = UUID("00000000-0000-0000-0000-000000000740")
NODE_RULE_LOW_STOCK = UUID("00000000-0000-0000-0000-000000000741")
NODE_RULE_CREDIT_CHECK = UUID("00000000-0000-0000-0000-000000000742")

# Code node UUIDs (0750-0752).
NODE_CODE_CUSTOMER = UUID("00000000-0000-0000-0000-000000000750")
NODE_CODE_PRODUCT = UUID("00000000-0000-0000-0000-000000000751")
NODE_CODE_ORDER = UUID("00000000-0000-0000-0000-000000000752")


def _json(value: Mapping[str, object]) -> str:
    """Serialize dict to JSON string for JSONB columns (bound as text, cast in SQL)."""
    return json.dumps(value, ensure_ascii=False)


async def _truncate(session: AsyncSession) -> None:
    """Clear all tables in FK-reverse order. CASCADE handles dependencies."""
    statements = [
        "TRUNCATE evolution.preference_pairs, evolution.datasets, evolution.training_runs CASCADE",
        "TRUNCATE evolution.feedback_signals CASCADE",
        "TRUNCATE harness.evolution_strategies, harness.quality_metrics, "
        "harness.audit_logs, harness.quotas CASCADE",
        "TRUNCATE trace.spans CASCADE",
        "TRUNCATE data.query_history, data.datasources CASCADE",
        "TRUNCATE knowledge.org_memories, knowledge.chunks, "
        "knowledge.documents, knowledge.ontology_nodes, knowledge.ontologies CASCADE",
        "TRUNCATE skills.assignments, skills.skill_versions, skills.skills CASCADE",
        "TRUNCATE agent.agent_skills, agent.sessions, agent.agents CASCADE",
        "TRUNCATE iam.permissions, iam.memberships, iam.users, "
        "iam.departments, iam.tenants CASCADE",
        # M2: mock external data (no FK to core schemas; CRM before ERP due to lead->opp ref).
        "TRUNCATE crm.activities, crm.opportunities, crm.leads CASCADE",
        "TRUNCATE erp.inventory, erp.orders, erp.customers, erp.products CASCADE",
    ]
    for stmt in statements:
        await session.execute(text(stmt))


async def _seed_iam(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO iam.tenants (id, name, slug, status, settings) "
            "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb))"
        ),
        {
            "p0": TENANT_ID,
            "p1": "Acme Corporation",
            "p2": "acme-corp",
            "p3": "active",
            "p4": _json({"plan": "enterprise", "region": "cn"}),
        },
    )
    print(f"  iam.tenants: 1 row (tenant={TENANT_ID})")

    await session.execute(
        text(
            "INSERT INTO iam.departments (id, tenant_id, name, parent_id) "
            "VALUES (:p0, :p1, :p2, NULL), (:p3, :p4, :p5, NULL)"
        ),
        {
            "p0": DEPT_RND,
            "p1": TENANT_ID,
            "p2": "Research & Development",
            "p3": DEPT_SALES,
            "p4": TENANT_ID,
            "p5": "Sales",
        },
    )
    print(f"  iam.departments: 2 rows (rnd={DEPT_RND}, sales={DEPT_SALES})")

    await session.execute(
        text(
            "INSERT INTO iam.users (id, tenant_id, email, name, role, status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5), "
            "(:p6, :p7, :p8, :p9, :p10, :p11), "
            "(:p12, :p13, :p14, :p15, :p16, :p17)"
        ),
        {
            "p0": USER_ADMIN,
            "p1": TENANT_ID,
            "p2": "admin@acme.com",
            "p3": "Alice Admin",
            "p4": "admin",
            "p5": "active",
            "p6": USER_MANAGER,
            "p7": TENANT_ID,
            "p8": "manager@acme.com",
            "p9": "Morgan Manager",
            "p10": "manager",
            "p11": "active",
            "p12": USER_EMPLOYEE,
            "p13": TENANT_ID,
            "p14": "employee@acme.com",
            "p15": "Evan Employee",
            "p16": "employee",
            "p17": "active",
        },
    )
    print("  iam.users: 3 rows (admin/manager/employee)")

    await session.execute(
        text(
            "INSERT INTO iam.memberships (user_id, department_id, role) "
            "VALUES (:p0, :p1, :p2), (:p3, :p4, :p5), (:p6, :p7, :p8)"
        ),
        {
            "p0": USER_ADMIN,
            "p1": DEPT_RND,
            "p2": "head",
            "p3": USER_MANAGER,
            "p4": DEPT_RND,
            "p5": "member",
            "p6": USER_EMPLOYEE,
            "p7": DEPT_SALES,
            "p8": "member",
        },
    )
    print("  iam.memberships: 3 rows")

    await session.execute(
        text(
            "INSERT INTO iam.permissions "
            '(tenant_id, role, resource, action, "constraint") '
            "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb)), "
            "(:p5, :p6, :p7, :p8, CAST(:p9 AS jsonb)), "
            "(:p10, :p11, :p12, :p13, CAST(:p14 AS jsonb)), "
            "(:p15, :p16, :p17, :p18, CAST(:p19 AS jsonb)), "
            "(:p20, :p21, :p22, :p23, CAST(:p24 AS jsonb)), "
            "(:p25, :p26, :p27, :p28, CAST(:p29 AS jsonb)), "
            "(:p30, :p31, :p32, :p33, CAST(:p34 AS jsonb)), "
            "(:p35, :p36, :p37, :p38, CAST(:p39 AS jsonb))"
        ),
        {
            "p0": TENANT_ID,
            "p1": "admin",
            "p2": "agent",
            "p3": "create",
            "p4": _json({}),
            "p5": TENANT_ID,
            "p6": "admin",
            "p7": "skill",
            "p8": "delete",
            "p9": _json({}),
            "p10": TENANT_ID,
            "p11": "manager",
            "p12": "skill",
            "p13": "execute",
            "p14": _json({"scope": "department"}),
            "p15": TENANT_ID,
            "p16": "manager",
            "p17": "datasource",
            "p18": "read",
            "p19": _json({"scope": "department"}),
            "p20": TENANT_ID,
            "p21": "employee",
            "p22": "skill",
            "p23": "read",
            "p24": _json({"scope": "personal"}),
            "p25": TENANT_ID,
            "p26": "employee",
            "p27": "agent",
            "p28": "execute",
            "p29": _json({"scope": "personal"}),
            "p30": TENANT_ID,
            "p31": "employee",
            "p32": "knowledge.contribution",
            "p33": "submit",
            "p34": _json({}),
            "p35": TENANT_ID,
            "p36": "admin",
            "p37": "knowledge.contribution",
            "p38": "review",
            "p39": _json({}),
        },
    )
    print("  iam.permissions: 8 rows")


async def _seed_agent(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO agent.agents "
            "(id, tenant_id, scope, owner_id, name, description, "
            "model_config, capability, status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, "
            "CAST(:p6 AS jsonb), CAST(:p7 AS jsonb), :p8), "
            "(:p9, :p10, :p11, :p12, :p13, :p14, "
            "CAST(:p15 AS jsonb), CAST(:p16 AS jsonb), :p17)"
        ),
        {
            "p0": AGENT_PERSONAL,
            "p1": TENANT_ID,
            "p2": "personal",
            "p3": USER_EMPLOYEE,
            "p4": "My Assistant",
            "p5": "Personal productivity agent for Evan",
            "p6": _json({"provider": "openai", "model": "gpt-4o-mini"}),
            "p7": _json({"tools": ["text2sql", "rag"]}),
            "p8": "active",
            "p9": AGENT_DEPARTMENT,
            "p10": TENANT_ID,
            "p11": "department",
            "p12": DEPT_RND,
            "p13": "R&D Team Agent",
            "p14": "Shared agent for the R&D department",
            "p15": _json({"provider": "anthropic", "model": "claude-3-5-sonnet"}),
            "p16": _json({"tools": ["rag"]}),
            "p17": "active",
        },
    )
    print(f"  agent.agents: 2 rows (personal={AGENT_PERSONAL}, dept={AGENT_DEPARTMENT})")

    await session.execute(
        text(
            "INSERT INTO agent.sessions "
            "(id, agent_id, tenant_id, thread_id, user_id, title, status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)"
        ),
        {
            "p0": SESSION_DEMO,
            "p1": AGENT_PERSONAL,
            "p2": TENANT_ID,
            "p3": "acme:agent:session:demo-1",
            "p4": USER_EMPLOYEE,
            "p5": "Demo session",
            "p6": "active",
        },
    )
    print(f"  agent.sessions: 1 row (session={SESSION_DEMO})")

    await session.execute(
        text(
            "INSERT INTO agent.agent_skills (agent_id, skill_id, enabled) "
            "VALUES (:p0, :p1, true), (:p2, :p3, true)"
        ),
        {
            "p0": AGENT_PERSONAL,
            "p1": SKILL_TEXT2SQL,
            "p2": AGENT_PERSONAL,
            "p3": SKILL_RAG,
        },
    )
    print("  agent.agent_skills: 2 rows")


async def _seed_skills(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO skills.skills "
            "(id, tenant_id, scope, owner_id, name, display_name, "
            "description, category, risk_level, guardrail, status, version) "
            "VALUES "
            "(:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, "
            "CAST(:p9 AS jsonb), :p10, :p11), "
            "(:p12, :p13, :p14, :p15, :p16, :p17, :p18, :p19, :p20, "
            "CAST(:p21 AS jsonb), :p22, :p23), "
            "(:p24, :p25, :p26, :p27, :p28, :p29, :p30, :p31, :p32, "
            "CAST(:p33 AS jsonb), :p34, :p35)"
        ),
        {
            "p0": SKILL_TEXT2SQL,
            "p1": TENANT_ID,
            "p2": "company",
            "p3": None,
            "p4": "text2sql",
            "p5": "Text to SQL",
            "p6": "Translate natural language questions to safe SQL queries.",
            "p7": "data_analysis",
            "p8": "medium",
            "p9": _json({"readonly": True}),
            "p10": "published",
            "p11": "0.1.0",
            "p12": SKILL_RAG,
            "p13": TENANT_ID,
            "p14": "company",
            "p15": None,
            "p16": "rag",
            "p17": "Knowledge RAG",
            "p18": "Retrieval-augmented generation over enterprise documents.",
            "p19": "knowledge_api",
            "p20": "low",
            "p21": _json({}),
            "p22": "published",
            "p23": "0.1.0",
            "p24": SKILL_CODE,
            "p25": TENANT_ID,
            "p26": "department",
            "p27": DEPT_RND,
            "p28": "code-interpreter",
            "p29": "Code Interpreter",
            "p30": "Execute Python code in a sandbox for data analysis tasks.",
            "p31": "data_analysis",
            "p32": "high",
            "p33": _json({"sandbox": True, "network": False}),
            "p34": "draft",
            "p35": "0.1.0",
        },
    )
    print("  skills.skills: 3 rows (text2sql/rag/code-interpreter)")

    await session.execute(
        text(
            "INSERT INTO skills.skill_versions "
            "(skill_id, version, yaml_content, changelog, created_by) "
            "VALUES (:p0, :p1, CAST(:p2 AS jsonb), :p3, :p4), "
            "(:p5, :p6, CAST(:p7 AS jsonb), :p8, :p9), "
            "(:p10, :p11, CAST(:p12 AS jsonb), :p13, :p14)"
        ),
        {
            "p0": SKILL_TEXT2SQL,
            "p1": "0.1.0",
            "p2": _json({"spec": {"name": "text2sql", "version": "0.1.0"}}),
            "p3": "Initial draft",
            "p4": USER_ADMIN,
            "p5": SKILL_RAG,
            "p6": "0.1.0",
            "p7": _json({"spec": {"name": "rag", "version": "0.1.0"}}),
            "p8": "Initial draft",
            "p9": USER_ADMIN,
            "p10": SKILL_CODE,
            "p11": "0.1.0",
            "p12": _json({"spec": {"name": "code-interpreter", "version": "0.1.0"}}),
            "p13": "Initial draft",
            "p14": USER_MANAGER,
        },
    )
    print("  skills.skill_versions: 3 rows")

    await session.execute(
        text(
            "INSERT INTO skills.assignments "
            "(skill_id, tenant_id, user_id, agent_id) "
            "VALUES (:p0, :p1, :p2, :p3)"
        ),
        {
            "p0": SKILL_TEXT2SQL,
            "p1": TENANT_ID,
            "p2": USER_EMPLOYEE,
            "p3": AGENT_PERSONAL,
        },
    )
    print("  skills.assignments: 1 row")


async def _seed_knowledge_base(session: AsyncSession) -> None:
    """Seed ontologies + documents + memory (ontology nodes seeded separately)."""
    await session.execute(
        text(
            "INSERT INTO knowledge.ontologies "
            "(id, tenant_id, name, version, status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4)"
        ),
        {
            "p0": ONTOLOGY_ERP,
            "p1": TENANT_ID,
            "p2": "Acme ERP Ontology",
            "p3": "1.0.0",
            "p4": "active",
        },
    )
    print(f"  knowledge.ontologies: 1 row (ontology={ONTOLOGY_ERP})")

    await session.execute(
        text(
            "INSERT INTO knowledge.documents "
            "(id, tenant_id, source_type, source_uri, title, "
            "content_hash, version, metadata, status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, "
            "CAST(:p7 AS jsonb), :p8), "
            "(:p9, :p10, :p11, :p12, :p13, :p14, :p15, "
            "CAST(:p16 AS jsonb), :p17)"
        ),
        {
            "p0": DOC_ERP_MANUAL,
            "p1": TENANT_ID,
            "p2": "markdown",
            "p3": "file://docs/erp-user-manual.md",
            "p4": "ERP操作手册",
            "p5": "sha256:erp_manual_v2",
            "p6": 2,
            "p7": _json({"pages": 45, "lang": "zh"}),
            "p8": "indexed",
            "p9": DOC_CRM_API,
            "p10": TENANT_ID,
            "p11": "markdown",
            "p12": "file://docs/crm-api-reference.md",
            "p13": "CRM API文档",
            "p14": "sha256:crm_api_v2",
            "p15": 2,
            "p16": _json({"crawled_at": "2026-06-01", "lang": "zh"}),
            "p17": "indexed",
        },
    )
    print("  knowledge.documents: 2 rows")

    await session.execute(
        text(
            "INSERT INTO knowledge.org_memories "
            "(id, tenant_id, scope, owner_id, memory_type, content, "
            "embedding, confidence, source) "
            "VALUES (:p0, :p1, :p2, NULL, :p3, :p4, NULL, :p5, :p6)"
        ),
        {
            "p0": MEMORY_PREF,
            "p1": TENANT_ID,
            "p2": "enterprise",
            "p3": "preference",
            "p4": "Admin prefers concise answers (under 200 words).",
            "p5": 0.9,
            "p6": "agent",
        },
    )
    print("  knowledge.org_memories: 1 row")


# --- Ontology nodes (full enterprise ontology) ---


def _ontology_node_rows() -> list[tuple[Any, ...]]:
    """Build the 49 ontology node rows for executemany insertion."""
    rows: list[tuple[Any, ...]] = []

    # 6 Object nodes.
    objects = [
        (NODE_OBJ_CUSTOMER, "Customer", {"table": "erp.customers", "description": "客户主数据"}),
        (NODE_OBJ_PRODUCT, "Product", {"table": "erp.products", "description": "产品主数据"}),
        (NODE_OBJ_ORDER, "Order", {"table": "erp.orders", "description": "销售订单"}),
        (NODE_OBJ_INVENTORY, "Inventory", {"table": "erp.inventory", "description": "库存记录"}),
        (NODE_OBJ_LEAD, "Lead", {"table": "crm.leads", "description": "销售线索"}),
        (
            NODE_OBJ_OPPORTUNITY,
            "Opportunity",
            {"table": "crm.opportunities", "description": "商机"},
        ),
    ]
    for node_id, name, props in objects:
        rows.append((node_id, ONTOLOGY_ERP, TENANT_ID, "object", name, None, _json(props)))

    # 32 Attribute nodes — (id, parent_id, name, properties).
    def attr(
        node_id: UUID, parent: UUID, name: str, column: str, zh: str, col_type: str
    ) -> tuple[Any, ...]:
        return (
            node_id,
            ONTOLOGY_ERP,
            TENANT_ID,
            "attribute",
            name,
            parent,
            _json({"column": column, "chinese_name": zh, "type": col_type}),
        )

    # Customer attributes (6).
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_NAME,
            NODE_OBJ_CUSTOMER,
            "Customer.name",
            "name",
            "客户名称",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_CODE,
            NODE_OBJ_CUSTOMER,
            "Customer.code",
            "code",
            "客户编码",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_INDUSTRY,
            NODE_OBJ_CUSTOMER,
            "Customer.industry",
            "industry",
            "行业",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_CONTACT,
            NODE_OBJ_CUSTOMER,
            "Customer.contact_name",
            "contact_name",
            "联系人",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_CREDIT,
            NODE_OBJ_CUSTOMER,
            "Customer.credit_limit",
            "credit_limit",
            "信用额度",
            "numeric",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_CUSTOMER_CREATED,
            NODE_OBJ_CUSTOMER,
            "Customer.created_at",
            "created_at",
            "创建时间",
            "timestamptz",
        )
    )
    # Product attributes (6).
    rows.append(
        attr(NODE_ATTR_PRODUCT_SKU, NODE_OBJ_PRODUCT, "Product.sku", "sku", "SKU", "varchar")
    )
    rows.append(
        attr(
            NODE_ATTR_PRODUCT_NAME, NODE_OBJ_PRODUCT, "Product.name", "name", "产品名称", "varchar"
        )
    )
    rows.append(
        attr(
            NODE_ATTR_PRODUCT_CATEGORY,
            NODE_OBJ_PRODUCT,
            "Product.category",
            "category",
            "类别",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_PRODUCT_PRICE,
            NODE_OBJ_PRODUCT,
            "Product.unit_price",
            "unit_price",
            "单价",
            "numeric",
        )
    )
    rows.append(
        attr(NODE_ATTR_PRODUCT_COST, NODE_OBJ_PRODUCT, "Product.cost", "cost", "成本", "numeric")
    )
    rows.append(
        attr(
            NODE_ATTR_PRODUCT_STATUS,
            NODE_OBJ_PRODUCT,
            "Product.status",
            "status",
            "状态",
            "varchar",
        )
    )
    # Order attributes (7).
    rows.append(
        attr(NODE_ATTR_ORDER_NO, NODE_OBJ_ORDER, "Order.order_no", "order_no", "订单号", "varchar")
    )
    rows.append(
        attr(
            NODE_ATTR_ORDER_CUSTOMER_ID,
            NODE_OBJ_ORDER,
            "Order.customer_id",
            "customer_id",
            "客户ID",
            "uuid",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_ORDER_PRODUCT_ID,
            NODE_OBJ_ORDER,
            "Order.product_id",
            "product_id",
            "产品ID",
            "uuid",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_ORDER_QUANTITY,
            NODE_OBJ_ORDER,
            "Order.quantity",
            "quantity",
            "数量",
            "integer",
        )
    )
    rows.append(
        attr(NODE_ATTR_ORDER_AMOUNT, NODE_OBJ_ORDER, "Order.amount", "amount", "金额", "numeric")
    )
    rows.append(
        attr(
            NODE_ATTR_ORDER_STATUS, NODE_OBJ_ORDER, "Order.status", "status", "订单状态", "varchar"
        )
    )
    rows.append(
        attr(
            NODE_ATTR_ORDER_DATE,
            NODE_OBJ_ORDER,
            "Order.order_date",
            "order_date",
            "下单日期",
            "date",
        )
    )
    # Inventory attributes (4).
    rows.append(
        attr(
            NODE_ATTR_INVENTORY_PRODUCT_ID,
            NODE_OBJ_INVENTORY,
            "Inventory.product_id",
            "product_id",
            "产品ID",
            "uuid",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_INVENTORY_WAREHOUSE,
            NODE_OBJ_INVENTORY,
            "Inventory.warehouse",
            "warehouse",
            "仓库",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_INVENTORY_QUANTITY,
            NODE_OBJ_INVENTORY,
            "Inventory.quantity",
            "quantity",
            "库存数量",
            "integer",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_INVENTORY_SAFETY,
            NODE_OBJ_INVENTORY,
            "Inventory.safety_stock",
            "safety_stock",
            "安全库存",
            "integer",
        )
    )
    # Lead attributes (4).
    rows.append(
        attr(
            NODE_ATTR_LEAD_NAME, NODE_OBJ_LEAD, "Lead.lead_name", "lead_name", "线索名称", "varchar"
        )
    )
    rows.append(
        attr(NODE_ATTR_LEAD_SOURCE, NODE_OBJ_LEAD, "Lead.source", "source", "来源", "varchar")
    )
    rows.append(
        attr(NODE_ATTR_LEAD_STATUS, NODE_OBJ_LEAD, "Lead.status", "status", "线索状态", "varchar")
    )
    rows.append(
        attr(
            NODE_ATTR_LEAD_CONVERTED,
            NODE_OBJ_LEAD,
            "Lead.converted_to_opportunity_id",
            "converted_to_opportunity_id",
            "转化商机ID",
            "uuid",
        )
    )
    # Opportunity attributes (5).
    rows.append(
        attr(
            NODE_ATTR_OPP_NAME,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity.opp_name",
            "opp_name",
            "商机名称",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_OPP_CUSTOMER_ID,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity.customer_id",
            "customer_id",
            "客户ID",
            "uuid",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_OPP_AMOUNT,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity.amount",
            "amount",
            "商机金额",
            "numeric",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_OPP_STAGE,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity.stage",
            "stage",
            "阶段",
            "varchar",
        )
    )
    rows.append(
        attr(
            NODE_ATTR_OPP_CLOSE_DATE,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity.expected_close_date",
            "expected_close_date",
            "预计关闭日期",
            "date",
        )
    )

    # 5 Relation nodes — parent = source object.
    def rel(node_id: UUID, parent: UUID, name: str, props: dict[str, object]) -> tuple[Any, ...]:
        return (node_id, ONTOLOGY_ERP, TENANT_ID, "relation", name, parent, _json(props))

    rows.append(
        rel(
            NODE_REL_CUSTOMER_ORDER,
            NODE_OBJ_CUSTOMER,
            "Customer→Order",
            {
                "from_object": "customer",
                "to_object": "order",
                "relation": "places",
                "type": "one_to_many",
                "fk": "orders.customer_id",
            },
        )
    )
    rows.append(
        rel(
            NODE_REL_ORDER_PRODUCT,
            NODE_OBJ_ORDER,
            "Order→Product",
            {
                "from_object": "order",
                "to_object": "product",
                "relation": "contains",
                "type": "many_to_one",
                "fk": "orders.product_id",
            },
        )
    )
    rows.append(
        rel(
            NODE_REL_PRODUCT_INVENTORY,
            NODE_OBJ_PRODUCT,
            "Product→Inventory",
            {
                "from_object": "product",
                "to_object": "inventory",
                "relation": "stocked_in",
                "type": "one_to_many",
                "fk": "inventory.product_id",
            },
        )
    )
    rows.append(
        rel(
            NODE_REL_LEAD_OPPORTUNITY,
            NODE_OBJ_LEAD,
            "Lead→Opportunity",
            {
                "from_object": "lead",
                "to_object": "opportunity",
                "relation": "converts_to",
                "type": "one_to_one",
                "fk": "leads.converted_to_opportunity_id",
            },
        )
    )
    rows.append(
        rel(
            NODE_REL_OPPORTUNITY_CUSTOMER,
            NODE_OBJ_OPPORTUNITY,
            "Opportunity→Customer",
            {
                "from_object": "opportunity",
                "to_object": "customer",
                "relation": "belongs_to",
                "type": "many_to_one",
                "fk": "opportunities.customer_id",
            },
        )
    )

    # 3 Rule nodes.
    def rule(node_id: UUID, name: str, props: dict[str, object]) -> tuple[Any, ...]:
        return (node_id, ONTOLOGY_ERP, TENANT_ID, "rule", name, None, _json(props))

    rows.append(
        rule(
            NODE_RULE_LARGE_ORDER,
            "大额订单审批",
            {"condition": "order.amount > 100000", "action": "require_approval", "role": "manager"},
        )
    )
    rows.append(
        rule(
            NODE_RULE_LOW_STOCK,
            "低库存告警",
            {"condition": "inventory.quantity < inventory.safety_stock", "action": "alert"},
        )
    )
    rows.append(
        rule(
            NODE_RULE_CREDIT_CHECK,
            "信用额度检查",
            {
                "condition": "customer.credit_limit - SUM(active_orders.amount) <= 0",
                "action": "block_new_orders",
            },
        )
    )

    # 3 Code nodes.
    def code(node_id: UUID, name: str, props: dict[str, object]) -> tuple[Any, ...]:
        return (node_id, ONTOLOGY_ERP, TENANT_ID, "code", name, None, _json(props))

    rows.append(
        code(
            NODE_CODE_CUSTOMER,
            "客户编码",
            {"pattern": "CUS-{industry}-{4-digit}", "example": "CUS-TECH-0001"},
        )
    )
    rows.append(
        code(
            NODE_CODE_PRODUCT,
            "产品SKU",
            {"pattern": "PRD-{category}-{3-digit}", "example": "PRD-ELEC-001"},
        )
    )
    rows.append(
        code(
            NODE_CODE_ORDER,
            "订单号",
            {"pattern": "ORD-{YYYYMMDD}-{3-digit}", "example": "ORD-20260101-001"},
        )
    )

    return rows


async def _seed_ontology_nodes(session: AsyncSession) -> None:
    """Insert the full enterprise ontology (49 nodes) replacing M1's 2 examples."""
    rows = _ontology_node_rows()
    sql = (
        "INSERT INTO knowledge.ontology_nodes "
        "(id, ontology_id, tenant_id, node_type, name, parent_id, properties) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, CAST(:p6 AS jsonb))"
    )
    # SQLAlchemy executemany: pass list of param dicts.
    param_dicts = [
        {"p0": r[0], "p1": r[1], "p2": r[2], "p3": r[3], "p4": r[4], "p5": r[5], "p6": r[6]}
        for r in rows
    ]
    await session.execute(text(sql), param_dicts)
    counts = {"object": 0, "attribute": 0, "relation": 0, "rule": 0, "code": 0}
    for r in rows:
        counts[r[3]] += 1
    print(
        f"  knowledge.ontology_nodes: {len(rows)} rows "
        f"(objects={counts['object']}, attrs={counts['attribute']}, "
        f"relations={counts['relation']}, rules={counts['rule']}, codes={counts['code']})"
    )


async def _seed_erp(session: AsyncSession) -> None:
    """Seed erp schema: 10 products + 10 customers + 15 orders + 10 inventory."""
    # Products (10) — includes 1 discontinued.
    products = [
        ("PRD-ELEC-001", "无线鼠标", "electronics", 99.00, 45.00, "active"),
        ("PRD-ELEC-002", "机械键盘", "electronics", 299.00, 150.00, "active"),
        ("PRD-ELEC-003", "27寸显示器", "electronics", 1599.00, 980.00, "active"),
        ("PRD-ELEC-004", "USB-C Hub", "electronics", 159.00, 78.00, "active"),
        ("PRD-OFF-001", "A4打印纸", "office", 45.00, 28.00, "active"),
        ("PRD-OFF-002", "订书机", "office", 35.00, 18.00, "active"),
        ("PRD-FUR-001", "人体工学椅", "furniture", 1299.00, 720.00, "active"),
        ("PRD-FUR-002", "升降桌", "furniture", 2499.00, 1450.00, "active"),
        ("PRD-OFF-003", "文件夹", "office", 12.00, 5.50, "active"),
        ("PRD-ELEC-005", "老式软驱", "electronics", 25.00, 20.00, "discontinued"),
    ]
    sql = (
        "INSERT INTO erp.products "
        "(id, sku, name, category, unit_price, cost, status) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)"
    )
    param_dicts = [
        {
            "p0": ERP_PRODUCT_IDS[i],
            "p1": sku,
            "p2": name,
            "p3": cat,
            "p4": price,
            "p5": cost,
            "p6": status,
        }
        for i, (sku, name, cat, price, cost, status) in enumerate(products)
    ]
    await session.execute(text(sql), param_dicts)
    print("  erp.products: 10 rows (1 discontinued)")

    # Customers (10) — includes 1 with credit_limit=0.
    customers = [
        ("CUS-TECH-0001", "科技先锋有限公司", "tech", "张伟", "zhangwei@tech.com", 500000),
        ("CUS-FINA-0001", "金融控股集团", "finance", "李娜", "lina@finance.com", 1000000),
        ("CUS-RETA-0001", "零售连锁公司", "retail", "王强", "wangqiang@retail.com", 200000),
        ("CUS-MANU-0001", "制造业集团", "manufacturing", "赵敏", "zhaomin@manu.com", 800000),
        ("CUS-EDU-0001", "教育科技公司", "education", "孙磊", "sunlei@edu.com", 100000),
        ("CUS-HEAL-0001", "医疗健康公司", "healthcare", "周涛", "zhoutao@health.com", 300000),
        ("CUS-LOGI-0001", "物流运输公司", "logistics", "吴丽", "wuli@logi.com", 150000),
        ("CUS-MEDI-0001", "媒体传播公司", "media", "郑浩", "zhenghao@media.com", 80000),
        ("CUS-CONS-0001", "咨询服务公司", "consulting", "王芳", "wangfang@consult.com", 0),
        ("CUS-ENER-0001", "能源开发公司", "energy", "陈杰", "chenjie@energy.com", 600000),
    ]
    sql = (
        "INSERT INTO erp.customers "
        "(id, code, name, industry, contact_name, contact_email, credit_limit) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)"
    )
    param_dicts = [
        {
            "p0": ERP_CUSTOMER_IDS[i],
            "p1": code,
            "p2": name,
            "p3": ind,
            "p4": contact,
            "p5": email,
            "p6": credit,
        }
        for i, (code, name, ind, contact, email, credit) in enumerate(customers)
    ]
    await session.execute(text(sql), param_dicts)
    print("  erp.customers: 10 rows (1 credit_limit=0)")

    # Orders (15) — includes 1 amount>100000, 1 overdue. Dates span May-June 2026.
    orders = [
        ("ORD-20260501-001", 0, 0, 10, 99.00, 990.00, "delivered", datetime(2026, 5, 1)),
        ("ORD-20260503-001", 1, 2, 5, 1599.00, 7995.00, "delivered", datetime(2026, 5, 3)),
        ("ORD-20260505-001", 2, 4, 50, 45.00, 2250.00, "delivered", datetime(2026, 5, 5)),
        ("ORD-20260508-001", 3, 6, 2, 1299.00, 2598.00, "delivered", datetime(2026, 5, 8)),
        ("ORD-20260510-001", 0, 1, 8, 299.00, 2392.00, "delivered", datetime(2026, 5, 10)),
        ("ORD-20260512-001", 4, 7, 1, 2499.00, 2499.00, "shipped", datetime(2026, 5, 12)),
        ("ORD-20260515-001", 5, 0, 20, 99.00, 1980.00, "delivered", datetime(2026, 5, 15)),
        ("ORD-20260518-001", 6, 3, 15, 159.00, 2385.00, "confirmed", datetime(2026, 5, 18)),
        ("ORD-20260520-001", 1, 7, 50, 2499.00, 124950.00, "confirmed", datetime(2026, 5, 20)),
        ("ORD-20260522-001", 7, 5, 10, 35.00, 350.00, "delivered", datetime(2026, 5, 22)),
        ("ORD-20260525-001", 8, 0, 5, 99.00, 495.00, "overdue", datetime(2026, 5, 25)),
        ("ORD-20260528-001", 2, 2, 3, 1599.00, 4797.00, "delivered", datetime(2026, 5, 28)),
        ("ORD-20260601-001", 9, 6, 3, 1299.00, 3897.00, "confirmed", datetime(2026, 6, 1)),
        ("ORD-20260605-001", 0, 4, 30, 45.00, 1350.00, "shipped", datetime(2026, 6, 5)),
        ("ORD-20260610-001", 3, 1, 6, 299.00, 1794.00, "pending", datetime(2026, 6, 10)),
    ]
    sql = (
        "INSERT INTO erp.orders "
        "(id, order_no, customer_id, product_id, quantity, unit_price, amount, status, order_date) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8)"
    )
    param_dicts = [
        {
            "p0": ERP_ORDER_IDS[i],
            "p1": order_no,
            "p2": ERP_CUSTOMER_IDS[cust_idx],
            "p3": ERP_PRODUCT_IDS[prod_idx],
            "p4": qty,
            "p5": unit_price,
            "p6": amount,
            "p7": status,
            "p8": order_date,
        }
        for i, (
            order_no,
            cust_idx,
            prod_idx,
            qty,
            unit_price,
            amount,
            status,
            order_date,
        ) in enumerate(orders)
    ]
    await session.execute(text(sql), param_dicts)
    print("  erp.orders: 15 rows (1 amount>100k, 1 overdue)")

    # Inventory (10) — includes 2 with quantity=0.
    inventory = [
        (0, "北京仓", 200, 50),
        (1, "北京仓", 150, 30),
        (2, "上海仓", 30, 20),
        (3, "上海仓", 80, 40),
        (4, "北京仓", 500, 100),
        (5, "广州仓", 0, 20),
        (6, "上海仓", 25, 15),
        (7, "北京仓", 10, 5),
        (8, "广州仓", 1000, 200),
        (9, "北京仓", 0, 0),
    ]
    sql = (
        "INSERT INTO erp.inventory (id, product_id, warehouse, quantity, safety_stock) "
        "VALUES (:p0, :p1, :p2, :p3, :p4)"
    )
    param_dicts = [
        {
            "p0": ERP_INVENTORY_IDS[i],
            "p1": ERP_PRODUCT_IDS[prod_idx],
            "p2": warehouse,
            "p3": qty,
            "p4": safety,
        }
        for i, (prod_idx, warehouse, qty, safety) in enumerate(inventory)
    ]
    await session.execute(text(sql), param_dicts)
    print("  erp.inventory: 10 rows (2 quantity=0)")


async def _seed_crm(session: AsyncSession) -> None:
    """Seed crm schema: 10 leads + 10 opportunities + 15 activities."""
    # Opportunities first (leads reference them via converted_to_opportunity_id).
    opportunities = [
        ("科技先锋年度合同", 0, 50000, "won", datetime(2026, 5, 15)),
        ("金融集团扩容项目", 1, 120000, "negotiation", datetime(2026, 7, 15)),
        ("零售连锁设备采购", 2, 35000, "proposal", datetime(2026, 7, 30)),
        ("制造业IT升级", 3, 85000, "qualification", datetime(2026, 8, 15)),
        ("教育科技合作", 4, 15000, "prospecting", datetime(2026, 9, 1)),
        ("医疗健康定制方案", 5, 60000, "proposal", datetime(2026, 7, 20)),
        ("物流公司批量采购", 6, 28000, "negotiation", datetime(2026, 7, 10)),
        ("媒体传播年度服务", 7, 18000, "prospecting", datetime(2026, 8, 30)),
        ("咨询服务试用", 8, 5000, "qualification", datetime(2026, 7, 25)),
        ("能源开发长期合作", 9, 200000, "prospecting", datetime(2026, 9, 15)),
    ]
    sql = (
        "INSERT INTO crm.opportunities "
        "(id, opp_name, customer_id, amount, stage, expected_close_date) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5)"
    )
    param_dicts = [
        {
            "p0": CRM_OPP_IDS[i],
            "p1": name,
            "p2": ERP_CUSTOMER_IDS[cust_idx],
            "p3": amount,
            "p4": stage,
            "p5": close_date,
        }
        for i, (name, cust_idx, amount, stage, close_date) in enumerate(opportunities)
    ]
    await session.execute(text(sql), param_dicts)
    print("  crm.opportunities: 10 rows (1 won)")

    # Leads (10) — lead 0 converted to opportunity 0.
    leads = [
        ("李雷咨询", "web", "converted", CRM_OPP_IDS[0]),
        ("韩梅梅", "referral", "qualified", None),
        ("王博士", "event", "contacted", None),
        ("张教授", "web", "new", None),
        ("刘总监", "referral", "qualified", None),
        ("陈经理", "web", "contacted", None),
        ("赵主管", "cold_call", "lost", None),
        ("孙总", "event", "qualified", None),
        ("周工", "web", "new", None),
        ("吴老板", "referral", "lost", None),
    ]
    sql = (
        "INSERT INTO crm.leads (id, lead_name, source, status, converted_to_opportunity_id) "
        "VALUES (:p0, :p1, :p2, :p3, :p4)"
    )
    param_dicts = [
        {"p0": CRM_LEAD_IDS[i], "p1": name, "p2": source, "p3": status, "p4": converted}
        for i, (name, source, status, converted) in enumerate(leads)
    ]
    await session.execute(text(sql), param_dicts)
    print("  crm.leads: 10 rows (1 converted)")

    # Activities (15) — across 2 months, distributed across opportunities.
    activities = [
        (0, "call", "初次电话联系，了解客户需求", "李雷", datetime(2026, 5, 2, 10, 0)),
        (0, "meeting", "产品演示会议", "韩梅梅", datetime(2026, 5, 8, 14, 0)),
        (0, "proposal", "发送报价单", "王博士", datetime(2026, 5, 12, 9, 30)),
        (1, "email", "跟进扩容需求", "刘总监", datetime(2026, 5, 20, 11, 0)),
        (1, "meeting", "高管洽谈", "陈经理", datetime(2026, 6, 3, 15, 0)),
        (2, "call", "确认采购清单", "赵主管", datetime(2026, 5, 18, 10, 30)),
        (2, "demo", "产品功能演示", "孙总", datetime(2026, 6, 10, 14, 0)),
        (3, "email", "发送IT升级方案", "周工", datetime(2026, 6, 15, 9, 0)),
        (3, "meeting", "技术评审会议", "吴老板", datetime(2026, 6, 20, 13, 0)),
        (4, "call", "初步接触", "李雷", datetime(2026, 6, 25, 10, 0)),
        (5, "meeting", "医疗定制需求讨论", "韩梅梅", datetime(2026, 6, 28, 14, 30)),
        (6, "email", "批量采购报价", "王博士", datetime(2026, 6, 5, 11, 0)),
        (7, "call", "年度服务续约沟通", "刘总监", datetime(2026, 6, 12, 10, 0)),
        (8, "demo", "试用产品演示", "陈经理", datetime(2026, 6, 18, 15, 0)),
        (9, "meeting", "长期合作战略会议", "赵主管", datetime(2026, 6, 22, 14, 0)),
    ]
    sql = (
        "INSERT INTO crm.activities "
        "(id, opportunity_id, activity_type, description, performed_by, activity_date) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5)"
    )
    param_dicts = [
        {
            "p0": CRM_ACTIVITY_IDS[i],
            "p1": CRM_OPP_IDS[opp_idx],
            "p2": act_type,
            "p3": desc,
            "p4": by,
            "p5": act_date,
        }
        for i, (opp_idx, act_type, desc, by, act_date) in enumerate(activities)
    ]
    await session.execute(text(sql), param_dicts)
    print("  crm.activities: 15 rows (across May-June)")


# --- Document chunks ---


def _chunk_contents() -> list[tuple[UUID, UUID, int, str, int, dict[str, object]]]:
    """Return (chunk_id, doc_id, index, content, token_count, metadata) for 6 chunks."""
    erp_chunks = [
        (
            CHUNK_ERP_1,
            0,
            "# ERP系统操作指南\n\n欢迎使用Acme ERP系统。本章节介绍系统的基本操作流程，包括登录、"
            "主界面导航、订单创建和库存查询功能。新用户请先完成管理员分配的权限配置，"
            "然后使用企业邮箱登录系统。",
            28,
        ),
        (
            CHUNK_ERP_2,
            1,
            "# 订单管理\n\n在ERP系统中创建销售订单时，需要选择客户、产品和填写数量。"
            "系统会自动计算订单金额并检查客户信用额度。如果订单金额超过10万元，"
            "需要提交经理审批。订单状态包括：待处理、已确认、已发货、已交付、逾期和已取消。",
            32,
        ),
        (
            CHUNK_ERP_3,
            2,
            "# 库存管理\n\n库存模块实时反映各仓库的产品库存数量。当库存低于安全库存水平时，"
            "系统会自动触发低库存告警。库存记录包含产品ID、仓库名称、当前数量和安全库存阈值。"
            " discontinued状态的产品不会出现在可售列表中。",
            30,
        ),
    ]
    crm_chunks = [
        (
            CHUNK_CRM_1,
            0,
            "# CRM API参考文档\n\nAcme CRM系统提供RESTful API用于管理销售线索、商机和客户活动。"
            "所有API请求需在Authorization头中携带Bearer Token进行认证。"
            "基础URL为 https://api.acme.com/crm/v1，响应格式为JSON。",
            28,
        ),
        (
            CHUNK_CRM_2,
            1,
            "# 线索管理API\n\nPOST /leads 创建销售线索，需提供lead_name、source字段。"
            "GET /leads/{id} 查询线索详情，"
            "返回lead_name、source、status和converted_to_opportunity_id。"
            "当线索状态变为converted时，系统自动创建对应的商机记录。",
            30,
        ),
        (
            CHUNK_CRM_3,
            2,
            "# 商机管理API\n\nGET /opportunities 列出所有商机，支持按stage和customer_id过滤。"
            "商机阶段包括：prospecting、qualification、proposal、negotiation、won、lost。"
            "POST /opportunities/{id}/activities 记录跟进活动，"
            "activity_type可选call、email、meeting、demo。",
            32,
        ),
    ]
    rows: list[tuple[UUID, UUID, int, str, int, dict[str, object]]] = []
    for chunk_id, idx, content, tokens in erp_chunks:
        rows.append(
            (chunk_id, DOC_ERP_MANUAL, idx, content, tokens, {"page": idx + 1, "type": "text"})
        )
    for chunk_id, idx, content, tokens in crm_chunks:
        rows.append(
            (chunk_id, DOC_CRM_API, idx, content, tokens, {"page": idx + 1, "type": "text"})
        )
    return rows


async def _seed_document_chunks(session: AsyncSession, embedder: Any | None) -> None:
    """Insert 6 chunks (3 per document). Embeds if embedder is available."""
    chunks = _chunk_contents()
    # Generate embeddings in batch if embedder available.
    embeddings: list[list[float] | None] = [None] * len(chunks)
    if embedder is not None:
        try:
            texts = [c[3] for c in chunks]
            vectors = await embedder.embed_batch(texts)
            embeddings = vectors
            print(f"  (generated {len(vectors)} embeddings via {embedder.model_name})")
        except Exception as exc:
            print(f"  WARNING: embedding generation failed ({exc}); inserting NULL")

    for i, (chunk_id, doc_id, idx, content, tokens, meta) in enumerate(chunks):
        emb = embeddings[i]
        await session.execute(
            text(
                "INSERT INTO knowledge.chunks "
                "(id, document_id, tenant_id, chunk_index, content, "
                "token_count, embedding, metadata) "
                "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, CAST(:p6 AS vector), "
                "CAST(:p7 AS jsonb))"
            ),
            {
                "p0": chunk_id,
                "p1": doc_id,
                "p2": TENANT_ID,
                "p3": idx,
                "p4": content,
                "p5": tokens,
                "p6": _vector_str(emb),
                "p7": _json(dict(meta)),
            },
        )
    print(f"  knowledge.chunks: {len(chunks)} rows (3 per document)")


def _vector_str(emb: list[float] | None) -> str | None:
    """Format embedding for pgvector CAST(:p AS vector). None → SQL NULL."""
    if emb is None:
        return None
    return "[" + ",".join(str(x) for x in emb) + "]"


async def _seed_data(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO data.datasources "
            "(id, tenant_id, name, source_type, connection, "
            "schema_mapping, access_mode, status) "
            "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb), "
            "CAST(:p5 AS jsonb), :p6, :p7), "
            "(:p8, :p9, :p10, :p11, CAST(:p12 AS jsonb), "
            "CAST(:p13 AS jsonb), :p14, :p15), "
            "(:p16, :p17, :p18, :p19, CAST(:p20 AS jsonb), "
            "CAST(:p21 AS jsonb), :p22, :p23)"
        ),
        {
            "p0": DS_ERP,
            "p1": TENANT_ID,
            "p2": "ERP Database",
            "p3": "postgresql",
            "p4": _json(
                {"connector": "erp", "host": "erp-db.internal", "port": 5432, "database": "erp"}
            ),
            "p5": _json({"customers": "customer", "orders": "sales_order"}),
            "p6": "read",
            "p7": "active",
            "p8": DS_CRM,
            "p9": TENANT_ID,
            "p10": "CRM Database",
            "p11": "postgresql",
            "p12": _json({"connector": "crm", "base_url": "https://crm.acme.com/api/v1"}),
            "p13": _json({}),
            "p14": "read",
            "p15": "active",
            "p16": DS_KNOWLEDGE,
            "p17": TENANT_ID,
            "p18": "Knowledge Base",
            "p19": "postgresql",
            "p20": _json({"connector": "knowledge", "schema": "knowledge"}),
            "p21": _json({}),
            "p22": "read",
            "p23": "active",
        },
    )
    print("  data.datasources: 3 rows (ERP + CRM + Knowledge, connector-tagged)")

    await session.execute(
        text(
            "INSERT INTO data.query_history "
            "(id, tenant_id, datasource_id, user_id, natural_query, "
            "generated_sql, executed, success) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, false, NULL)"
        ),
        {
            "p0": QUERY_DEMO,
            "p1": TENANT_ID,
            "p2": DS_ERP,
            "p3": USER_EMPLOYEE,
            "p4": "Top 5 customers by sales last month",
            "p5": (
                "SELECT c.name, SUM(o.amount) AS total "
                "FROM erp.customers c JOIN erp.orders o ON c.id = o.customer_id "
                "WHERE o.order_date >= date_trunc('month', now()) "
                "- interval '1 month' "
                "GROUP BY c.name ORDER BY total DESC LIMIT 5"
            ),
        },
    )
    print("  data.query_history: 1 row")


async def _seed_harness(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO harness.quotas "
            "(id, tenant_id, scope, owner_id, period, token_limit, "
            "token_used, reset_at) "
            "VALUES (:p0, :p1, :p2, NULL, :p3, :p4, :p5, :p6)"
        ),
        {
            "p0": QUOTA_TENANT,
            "p1": TENANT_ID,
            "p2": "company",
            "p3": "monthly",
            "p4": 1_000_000,
            "p5": 0,
            "p6": datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC),
        },
    )
    print("  harness.quotas: 1 row")


def _build_embedder(config: AppConfig) -> Any | None:
    """Construct an OpenAIEmbedder if API key is configured, else None."""
    if config.embedding.api_key is None:
        return None
    from eaos.infra.vector.embedder import OpenAIEmbedder

    return OpenAIEmbedder(config.embedding)


async def main() -> None:
    """Seed the database with demo data. Idempotent (truncates first)."""
    config = AppConfig()
    print(f"Seeding database at {config.db.url}")
    client = PgClient(config.db)
    embedder = _build_embedder(config)
    if embedder is None:
        print("  (EAOS_EMBEDDING__API_KEY not set; chunks will have NULL embeddings)")
    try:
        async with client.session() as session:
            print("Truncating existing data...")
            await _truncate(session)

            print("Inserting demo data...")
            await _seed_iam(session)
            await _seed_agent(session)
            await _seed_skills(session)
            await _seed_knowledge_base(session)
            await _seed_ontology_nodes(session)
            await _seed_document_chunks(session, embedder)
            await _seed_erp(session)
            await _seed_crm(session)
            await _seed_data(session)
            await _seed_harness(session)

        print("Seed completed successfully.")
        print(f"  tenant={TENANT_ID}")
        print(f"  users: admin={USER_ADMIN} manager={USER_MANAGER} employee={USER_EMPLOYEE}")
        print(f"  agents: personal={AGENT_PERSONAL} department={AGENT_DEPARTMENT}")
        print(f"  skills: text2sql={SKILL_TEXT2SQL} rag={SKILL_RAG} code={SKILL_CODE}")
        print(f"  ontology: {ONTOLOGY_ERP}")
        print(f"  datasources: erp={DS_ERP} crm={DS_CRM} knowledge={DS_KNOWLEDGE}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
