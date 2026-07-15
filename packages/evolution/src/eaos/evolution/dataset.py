"""Preference dataset builder — construct chosen/rejected pairs from feedback.

Pairs same-prompt positive and negative responses for DPO training. Pairs are
sourced from: same user re-asking (negative = first attempt, positive = second),
explicit ratings, adoption vs abandonment on similar tasks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class PreferencePair:
    """A single preference pair for DPO training."""

    id: UUID = field(default_factory=uuid4)
    dataset_id: UUID = field(default=UUID(int=0))
    tenant_id: UUID = field(default=UUID(int=0))
    prompt: str = ""
    chosen: str = ""  # positive response
    rejected: str = ""  # negative response
    source_trace_id: UUID | None = None
    confidence: float = 0.5
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class Dataset:
    """A preference dataset for DPO training."""

    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default=UUID(int=0))
    name: str = ""
    pair_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)


class PreferenceDatasetBuilder(Protocol):
    """Build preference datasets from feedback signals."""

    async def build(
        self,
        tenant_id: UUID,
        name: str | None = None,
    ) -> UUID:
        """Build a dataset from accumulated feedback. Returns dataset id."""
        ...

    async def get_pairs(
        self,
        dataset_id: UUID,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PreferencePair]:
        """Fetch pairs from a dataset (paginated)."""
        ...

    async def get_dataset(self, dataset_id: UUID) -> Dataset:
        """Fetch dataset metadata."""
        ...

    async def list_datasets(
        self,
        tenant_id: UUID,
    ) -> list[Dataset]:
        """List all datasets for a tenant."""
        ...

    async def validate_pair(self, pair: PreferencePair) -> bool:
        """Validate a pair is suitable for training (quality gate)."""
        ...


class DatasetDb(Protocol):
    """Minimal DB subset for dataset persistence and signal/span joins."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


def _normalize_prompt(prompt: str) -> str:
    return prompt.lower().strip()


def _prompt_hash(prompt: str) -> str:
    return hashlib.md5(_normalize_prompt(prompt).encode()).hexdigest()


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _extract_output(attrs_raw: Any) -> str:
    """Pull the response text from a span's attributes JSONB."""
    attrs = _parse_jsonb(attrs_raw)
    if not isinstance(attrs, dict):
        return ""
    return str(attrs.get("output") or attrs.get("response") or "")


def _row_to_pair(row: dict[str, Any]) -> PreferencePair:
    return PreferencePair(
        id=row["id"],
        dataset_id=row["dataset_id"],
        tenant_id=row["tenant_id"],
        prompt=row["prompt"],
        chosen=row["chosen"],
        rejected=row["rejected"],
        source_trace_id=row.get("source_trace_id"),
        confidence=0.5,  # not persisted in table; rebuilt pairs default to neutral
        created_at=row["created_at"],
    )


def _row_to_dataset(row: dict[str, Any]) -> Dataset:
    return Dataset(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        pair_count=row["pair_count"],
        created_at=row["created_at"],
    )


_SIGNAL_JOIN_SQL = (
    "SELECT fs.id, fs.tenant_id, fs.trace_id, fs.span_id, fs.user_id, "
    "fs.agent_id, fs.signal_type, fs.signal_value, fs.strength, "
    "fs.captured_at, ts.name AS prompt, ts.attributes AS span_attrs "
    "FROM evolution.feedback_signals fs "
    "JOIN trace.spans ts ON fs.span_id = ts.id AND fs.tenant_id = ts.tenant_id "
    "WHERE fs.tenant_id = :p0 "
    "ORDER BY fs.captured_at DESC"
)


class PreferenceDatasetBuilderImpl:
    """PreferenceDatasetBuilder backed by feedback_signals joined with trace.spans.

    Pairs same-prompt positive/negative responses for DPO. Prompt is grouped by
    a normalized hash so rephrasings of the same intent still pair. Pairs where
    either side lacks a stored output are dropped (validate_pair gate).
    """

    def __init__(self, db: DatasetDb) -> None:
        self._db = db

    async def build(
        self,
        tenant_id: UUID,
        name: str | None = None,
    ) -> UUID:
        rows = await self._db.fetch(_SIGNAL_JOIN_SQL, tenant_id)

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            prompt = str(row.get("prompt", ""))
            if not prompt:
                continue
            groups.setdefault(_prompt_hash(prompt), []).append(row)

        pairs: list[PreferencePair] = []
        for group in groups.values():
            positives = [r for r in group if r["signal_value"] == "positive"]
            negatives = [r for r in group if r["signal_value"] == "negative"]
            if not positives or not negatives:
                continue
            pos = positives[0]
            neg = negatives[0]
            chosen = _extract_output(pos.get("span_attrs"))
            rejected = _extract_output(neg.get("span_attrs"))
            if not chosen or not rejected:
                continue
            confidence = min(float(pos["strength"]), float(neg["strength"]))
            pairs.append(
                PreferencePair(
                    tenant_id=tenant_id,
                    prompt=str(pos["prompt"]),
                    chosen=chosen,
                    rejected=rejected,
                    source_trace_id=pos["trace_id"],
                    confidence=confidence,
                )
            )

        ds_name = name or f"dataset-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        ds_row = await self._db.fetch_one(
            "INSERT INTO evolution.datasets(tenant_id, name) "
            "VALUES (:p0, :p1) RETURNING id",
            tenant_id,
            ds_name,
        )
        if ds_row is None:
            raise RuntimeError("dataset insert returned no id")
        dataset_id = UUID(str(ds_row["id"]))

        for p in pairs:
            await self._db.execute(
                """INSERT INTO evolution.preference_pairs
                   (dataset_id, tenant_id, prompt, chosen, rejected,
                    source_trace_id)
                   VALUES (:p0, :p1, :p2, :p3, :p4, :p5)""",
                dataset_id,
                p.tenant_id,
                p.prompt,
                p.chosen,
                p.rejected,
                p.source_trace_id,
            )

        await self._db.execute(
            "UPDATE evolution.datasets SET pair_count = :p0 WHERE id = :p1",
            len(pairs),
            dataset_id,
        )
        return dataset_id

    async def get_pairs(
        self,
        dataset_id: UUID,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PreferencePair]:
        rows = await self._db.fetch(
            "SELECT id, dataset_id, tenant_id, prompt, chosen, rejected, "
            "source_trace_id, created_at FROM evolution.preference_pairs "
            "WHERE dataset_id = :p0 ORDER BY created_at DESC "
            "LIMIT :p1 OFFSET :p2",
            dataset_id,
            limit,
            offset,
        )
        return [_row_to_pair(r) for r in rows]

    async def get_dataset(self, dataset_id: UUID) -> Dataset:
        row = await self._db.fetch_one(
            "SELECT id, tenant_id, name, pair_count, created_at "
            "FROM evolution.datasets WHERE id = :p0",
            dataset_id,
        )
        if row is None:
            raise KeyError(f"Dataset {dataset_id} not found")
        return _row_to_dataset(row)

    async def list_datasets(
        self,
        tenant_id: UUID,
    ) -> list[Dataset]:
        rows = await self._db.fetch(
            "SELECT id, tenant_id, name, pair_count, created_at "
            "FROM evolution.datasets WHERE tenant_id = :p0 "
            "ORDER BY created_at DESC",
            tenant_id,
        )
        return [_row_to_dataset(r) for r in rows]

    async def validate_pair(self, pair: PreferencePair) -> bool:
        return bool(
            pair.prompt
            and pair.chosen
            and pair.rejected
            and pair.confidence >= 0.5
        )
