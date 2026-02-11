"""Tests for the smart delegate tool."""

import pytest
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_smart_delegate import (
    assess_complexity,
    select_model,
    format_announcement,
    discover_models,
    SmartDelegateTool,
    MODEL_TIERS,
    TIER_RANK,
    ComplexityAssessment,
    ModelChoice,
)


class TestComplexityAssessment:
    """Test task complexity analysis."""

    def test_simple_task_low_score(self):
        result = assess_complexity("what is the weather?")
        assert result.score < 0.35
        assert result.tier in ("low", "fast")

    def test_hello_is_simple(self):
        result = assess_complexity("hello")
        assert result.score < 0.3

    def test_moderate_task(self):
        result = assess_complexity("write a function to parse CSV files and handle edge cases")
        assert result.tier in ("mid", "high")
        assert result.score >= 0.3

    def test_complex_architecture_task(self):
        result = assess_complexity(
            "Design a microservices architecture for a payment processing system. "
            "Consider scalability, security audit requirements, and distributed "
            "transaction handling. Compare trade-offs between event sourcing and CQRS."
        )
        assert result.score >= 0.6
        assert result.tier in ("xhigh", "high")
        assert len(result.signal_matches) > 0

    def test_complex_refactoring(self):
        result = assess_complexity(
            "Refactor the entire authentication module to use OAuth2. "
            "This is a critical production system. Plan and implement the migration."
        )
        assert result.score >= 0.55
        assert result.tier in ("xhigh", "high")

    def test_debug_task_mid(self):
        result = assess_complexity("debug this error in the login handler and fix the bug")
        assert result.tier in ("mid", "high")

    def test_multi_step_task(self):
        result = assess_complexity(
            "Research the best approach for implementing real-time notifications, "
            "then implement a WebSocket server, add authentication middleware, "
            "write integration tests, and deploy to production."
        )
        assert result.score >= 0.5

    def test_code_in_task_increases_complexity(self):
        result = assess_complexity(
            "analyze this code:\n```python\ndef process(data):\n    return data\n```\nand optimize it"
        )
        # Code presence should bump score
        assert result.score >= 0.3

    def test_word_count_affects_score(self):
        short = assess_complexity("list files")
        long_task = assess_complexity(
            "I need you to analyze the current database schema, identify performance bottlenecks "
            "in the query patterns, propose index optimizations, review the ORM usage patterns "
            "across all models, suggest N+1 query fixes, evaluate whether we should add read "
            "replicas, and provide a migration plan that can be executed with zero downtime. "
            "Also compare PostgreSQL vs CockroachDB for our use case."
        )
        assert long_task.score > short.score

    def test_returns_reasons(self):
        result = assess_complexity("implement a complete OAuth2 authentication system from scratch")
        assert len(result.reasons) > 0
        assert result.word_count > 0


class TestModelSelection:
    """Test model selection logic."""

    MOCK_MODELS = [
        {"id": "opus-4.6-thinking", "name": "Claude 4.6 Opus (Thinking)"},
        {"id": "opus-4.6", "name": "Claude 4.6 Opus"},
        {"id": "gpt-5.3-codex-high", "name": "GPT-5.3 Codex High"},
        {"id": "gpt-5.3-codex", "name": "GPT-5.3 Codex"},
        {"id": "sonnet-4.5-thinking", "name": "Claude 4.5 Sonnet (Thinking)"},
        {"id": "sonnet-4.5", "name": "Claude 4.5 Sonnet"},
        {"id": "gemini-3-flash", "name": "Gemini 3 Flash"},
        {"id": "grok", "name": "Grok"},
    ]

    def test_xhigh_selects_thinking_model(self):
        complexity = ComplexityAssessment(
            score=0.85, tier="xhigh", reasons=["Very complex"],
            word_count=50, signal_matches=["architecture"]
        )
        choice = select_model(complexity, self.MOCK_MODELS)
        assert choice.tier == "xhigh"
        assert "thinking" in choice.model_id or "xhigh" in choice.model_id

    def test_high_selects_strong_model(self):
        complexity = ComplexityAssessment(
            score=0.65, tier="high", reasons=["Complex"],
            word_count=30, signal_matches=["refactor"]
        )
        choice = select_model(complexity, self.MOCK_MODELS)
        assert choice.tier == "high"

    def test_mid_selects_balanced_model(self):
        complexity = ComplexityAssessment(
            score=0.4, tier="mid", reasons=["Moderate"],
            word_count=20, signal_matches=[]
        )
        choice = select_model(complexity, self.MOCK_MODELS)
        assert choice.tier in ("mid",)

    def test_fast_selects_quick_model(self):
        complexity = ComplexityAssessment(
            score=0.1, tier="fast", reasons=["Simple"],
            word_count=5, signal_matches=[]
        )
        choice = select_model(complexity, self.MOCK_MODELS)
        assert choice.tier == "fast"

    def test_exclude_model(self):
        complexity = ComplexityAssessment(
            score=0.85, tier="xhigh", reasons=["Complex"],
            word_count=50, signal_matches=[]
        )
        choice = select_model(complexity, self.MOCK_MODELS, exclude_model="opus-4.6-thinking")
        assert choice.model_id != "opus-4.6-thinking"

    def test_forced_tier(self):
        complexity = ComplexityAssessment(
            score=0.1, tier="fast", reasons=["Simple"],
            word_count=5, signal_matches=[]
        )
        choice = select_model(complexity, self.MOCK_MODELS, preferred_tier="high")
        assert choice.tier == "high"

    def test_fallback_when_no_tier_match(self):
        # Only provide models not in any tier
        weird_models = [{"id": "custom-model-v1", "name": "Custom Model"}]
        complexity = ComplexityAssessment(
            score=0.5, tier="mid", reasons=["Moderate"],
            word_count=20, signal_matches=[]
        )
        choice = select_model(complexity, weird_models)
        assert choice.model_id == "custom-model-v1"
        assert any("Fallback" in r or "fallback" in r.lower() for r in choice.reasons)


class TestAnnouncement:
    """Test announcement formatting."""

    def test_xhigh_announcement(self):
        complexity = ComplexityAssessment(
            score=0.85, tier="xhigh", reasons=["Very complex"],
            word_count=50, signal_matches=["architecture", "scalability"]
        )
        choice = ModelChoice(
            model_id="opus-4.6-thinking",
            model_name="Claude 4.6 Opus (Thinking)",
            tier="xhigh",
            reasons=["Selected: Claude 4.6 Opus (Thinking)"],
            available_models=["opus-4.6-thinking", "sonnet-4.5"],
        )
        text = format_announcement(complexity, choice)
        assert "Claude 4.6 Opus (Thinking)" in text
        assert "Maximum Reasoning" in text
        assert "opus-4.6-thinking" in text
        assert "very high" in text.lower() or "85%" in text

    def test_low_announcement(self):
        complexity = ComplexityAssessment(
            score=0.15, tier="fast", reasons=["Simple task"],
            word_count=5, signal_matches=[]
        )
        choice = ModelChoice(
            model_id="gemini-3-flash",
            model_name="Gemini 3 Flash",
            tier="fast",
            reasons=["Fast model is sufficient"],
            available_models=["gemini-3-flash"],
        )
        text = format_announcement(complexity, choice)
        assert "Gemini 3 Flash" in text
        assert "Fast" in text


class TestModelTiersConsistency:
    """Verify tier definitions are consistent."""

    def test_all_tiers_have_models(self):
        for tier in TIER_RANK:
            assert tier in MODEL_TIERS
            assert len(MODEL_TIERS[tier]) > 0

    def test_no_duplicate_models_across_tiers(self):
        all_models = []
        for models in MODEL_TIERS.values():
            all_models.extend(models)
        assert len(all_models) == len(set(all_models)), "Duplicate model across tiers"


class TestSmartDelegateToolInit:
    """Test SmartDelegateTool initialization."""

    def test_default_init(self):
        tool = SmartDelegateTool()
        assert tool._cursor_agent_path is not None

    def test_config_path_override(self):
        tool = SmartDelegateTool(config={"cursor_agent_path": "/custom/path"})
        assert tool._cursor_agent_path == "/custom/path"

    def test_delegate_config_path(self):
        tool = SmartDelegateTool(config={"delegate": {"cursor_agent_path": "/delegate/path"}})
        assert tool._cursor_agent_path == "/delegate/path"


class TestModelDiscovery:
    """Test model discovery (may hit real cursor-agent)."""

    def test_discover_returns_list(self):
        models = discover_models()
        # Should return a list (may be empty if cursor-agent not available)
        assert isinstance(models, list)

    def test_discover_models_have_id_and_name(self):
        models = discover_models()
        for m in models:
            assert "id" in m
            assert "name" in m


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
