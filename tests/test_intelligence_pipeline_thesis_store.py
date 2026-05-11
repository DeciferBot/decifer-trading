"""
tests/test_intelligence_pipeline_thesis_store.py

Tests that run_intelligence_pipeline.py correctly includes thesis_store
as Step 4, after theme_activation (Step 3).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, call, patch


class TestIntelligencePipelineThesisStore:
    def test_pipeline_imports_generate_thesis_store(self):
        import run_intelligence_pipeline
        assert hasattr(run_intelligence_pipeline, "generate_thesis_store")

    def test_thesis_store_step_called_after_theme_activation(self):
        call_order: list[str] = []

        def _mock_feed():
            call_order.append("generate_feed")
            m = MagicMock()
            m.candidates = []
            return m

        def _mock_economic():
            call_order.append("generate_economic_intelligence")
            return ({}, {})

        def _mock_activation():
            call_order.append("generate_theme_activation")
            return {"activation_summary": {"activated": 0, "total_themes": 0}}

        def _mock_thesis():
            call_order.append("generate_thesis_store")
            return {"thesis_summary": {"total_theses": 0}, "unavailable_sources": []}

        import run_intelligence_pipeline as rip
        with (
            patch.object(rip, "generate_feed", side_effect=_mock_feed),
            patch.object(rip, "generate_economic_intelligence", side_effect=_mock_economic),
            patch.object(rip, "generate_theme_activation", side_effect=_mock_activation),
            patch.object(rip, "generate_thesis_store", side_effect=_mock_thesis),
        ):
            rip.run()

        assert call_order == [
            "generate_feed",
            "generate_economic_intelligence",
            "generate_theme_activation",
            "generate_thesis_store",
        ]

    def test_thesis_store_step_does_not_crash_on_missing_inputs(self, tmp_path):
        import thesis_store as ts
        original_output = ts._OUTPUT_PATH
        try:
            ts._OUTPUT_PATH = str(tmp_path / "thesis_store.json")
            result = ts.generate_thesis_store(output_path=str(tmp_path / "thesis_store.json"))
        finally:
            ts._OUTPUT_PATH = original_output

        assert isinstance(result, dict)
        assert "unavailable_sources" in result
        assert len(result["unavailable_sources"]) > 0
