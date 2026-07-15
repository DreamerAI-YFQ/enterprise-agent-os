"""Verify evolution Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.evolution.dataset import PreferenceDatasetBuilder, PreferencePair
from eaos.evolution.feedback import FeedbackCollector, FeedbackSignal, SignalType
from eaos.evolution.guardrail import GuardrailChecker, GuardrailResult
from eaos.evolution.pipeline import EvolutionPipeline
from eaos.evolution.shadow import ShadowTrafficManager
from eaos.evolution.trainer import DPOTrainer, TrainingRun


class TestFeedback:
    def test_signaltype_values(self) -> None:
        assert SignalType.ADOPTED.value == "adopted"
        assert SignalType.USED_UNCHANGED.value == "used_unchanged"
        assert SignalType.REASKED.value == "reasked"
        assert SignalType.ABANDONED.value == "abandoned"
        assert SignalType.MODIFIED.value == "modified"

    def test_signal_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(FeedbackSignal)}
        assert {
            "id",
            "tenant_id",
            "trace_id",
            "span_id",
            "user_id",
            "agent_id",
            "signal_type",
            "signal_value",
            "strength",
            "captured_at",
        } <= fields

    def test_collector_methods(self) -> None:
        for method in ("collect_from_session", "collect_from_trace", "batch_save"):
            assert hasattr(FeedbackCollector, method)


class TestDataset:
    def test_preferencepair_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(PreferencePair)}
        assert {
            "id",
            "dataset_id",
            "tenant_id",
            "prompt",
            "chosen",
            "rejected",
            "source_trace_id",
            "created_at",
        } <= fields

    def test_builder_methods(self) -> None:
        for method in ("build", "get_pairs"):
            assert hasattr(PreferenceDatasetBuilder, method)


class TestTrainer:
    def test_trainingrun_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(TrainingRun)}
        assert {
            "id",
            "tenant_id",
            "dataset_id",
            "base_model",
            "method",
            "status",
            "metrics",
            "model_artifact_path",
            "started_at",
            "completed_at",
        } <= fields

    def test_trainer_methods(self) -> None:
        assert hasattr(DPOTrainer, "train")


class TestGuardrail:
    def test_result_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(GuardrailResult)}
        assert {"passed", "reason", "details"} <= fields

    def test_checker_methods(self) -> None:
        for method in ("safety_benchmark", "perf_compare"):
            assert hasattr(GuardrailChecker, method)


class TestShadow:
    def test_methods(self) -> None:
        for method in ("start", "evaluate", "stop"):
            assert hasattr(ShadowTrafficManager, method)


class TestPipeline:
    def test_methods(self) -> None:
        assert hasattr(EvolutionPipeline, "run")
