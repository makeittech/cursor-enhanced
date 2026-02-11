"""
Smart Delegate Tool - Delegate complex tasks to optimal models with clean context

Core cursor-enhanced skill: when a task is complex, delegate it to a more capable
agent with a cleaner context (only the task info). The tool:
1. Discovers available models via `cursor-agent --list-models`
2. Assesses task complexity and selects the optimal model
3. Announces the choice and reasoning to the user
4. Runs the sub-agent with clean context (task only, no history noise)
5. Returns the response

Model tiers (highest to lowest capability):
  - XHIGH: opus-4.6-thinking, gpt-5.3-codex-xhigh, gpt-5.2-codex-xhigh — deep reasoning, architecture
  - HIGH:  opus-4.6, gpt-5.3-codex-high, gpt-5.2-codex-high, gpt-5.2-high — complex code, analysis
  - MID:   sonnet-4.5-thinking, gpt-5.2-codex, gpt-5.1-high — moderate complexity
  - LOW:   sonnet-4.5, gemini-3-pro, gpt-5.2 — simple tasks, Q&A
  - FAST:  gemini-3-flash, grok, *-fast — quick answers, low latency
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("cursor_enhanced.smart_delegate")

# ── Model tier definitions ──────────────────────────────────────────────
# Order within a tier = preference (first = preferred)

MODEL_TIERS: Dict[str, List[str]] = {
    "xhigh": [
        "opus-4.6-thinking",
        "gpt-5.3-codex-xhigh",
        "gpt-5.3-codex-xhigh-fast",
        "gpt-5.2-codex-xhigh",
        "gpt-5.1-codex-max-high",
        "gpt-5.1-codex-max",
        "opus-4.5-thinking",
    ],
    "high": [
        "opus-4.6",
        "gpt-5.3-codex-high",
        "gpt-5.3-codex-high-fast",
        "gpt-5.2-codex-high",
        "gpt-5.2-high",
        "gpt-5.1-high",
        "opus-4.5",
    ],
    "mid": [
        "sonnet-4.5-thinking",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.2",
        "sonnet-4.5",
    ],
    "low": [
        "gemini-3-pro",
        "gpt-5.3-codex-low",
        "gpt-5.2-codex-low",
        "grok",
    ],
    "fast": [
        "gemini-3-flash",
        "gpt-5.3-codex-fast",
        "gpt-5.3-codex-low-fast",
        "gpt-5.2-codex-fast",
        "gpt-5.2-codex-low-fast",
    ],
}

# Reverse lookup: model_id → tier
_MODEL_TO_TIER: Dict[str, str] = {}
for _tier, _models in MODEL_TIERS.items():
    for _m in _models:
        _MODEL_TO_TIER[_m] = _tier

# Tier rank (higher = more capable)
TIER_RANK = {"xhigh": 5, "high": 4, "mid": 3, "low": 2, "fast": 1}

# ── Complexity signals ──────────────────────────────────────────────────

# Keywords / patterns that push complexity UP
HIGH_COMPLEXITY_SIGNALS = [
    r"\barchitect(?:ure)?\b",
    r"\bdesign\s+(?:system|pattern|decision)",
    r"\brefactor(?:ing)?\b.*(?:large|entire|whole|major)",
    r"\bmigrat(?:e|ion)\b",
    r"\boptimiz(?:e|ation)\b.*(?:performance|algorithm|query)",
    r"\bsecurity\s+(?:audit|review|analysis)",
    r"\bscalability\b",
    r"\bconcurrency\b",
    r"\bdistributed\b",
    r"\bmicroservices?\b",
    r"\binfrastructure\b",
    r"\bkubernetes|k8s|terraform|ansible\b",
    r"\bdeep\s+(?:analysis|dive|review|investigation)\b",
    r"\bcomplex\b",
    r"\bcritical\b.*(?:bug|issue|problem|error)",
    r"\bproduction\b.*(?:issue|bug|incident|outage)",
    r"\bwrite\s+(?:a\s+)?(?:full|complete|comprehensive)\b",
    r"\bfrom\s+scratch\b",
    r"\bimplement\s+(?:a\s+)?(?:new|full|complete)\b",
    r"\bmulti-?step\b",
    r"\bplan\s+and\s+implement\b",
    r"\banalyze\s+(?:and|then)\s+",
    r"\bresearch\s+(?:and|then)\s+",
    r"\bcompare\s+(?:and\s+)?(?:contrast|evaluate|choose)\b",
    r"\btrade-?offs?\b",
    r"\bpros?\s+(?:and|&)\s+cons?\b",
    r"\bdeploy\s+to\s+production\b",
    r"\bzero\s+downtime\b",
]

MID_COMPLEXITY_SIGNALS = [
    r"\bexplain\s+(?:how|why|the)\b",
    r"\bdebug(?:ging)?\b",
    r"\bfix\s+(?:this|the|a)\b.*\b(?:bug|error|issue)\b",
    r"\bwrite\s+(?:a\s+)?(?:function|class|module|script|test)\b",
    r"\badd\s+(?:a\s+)?(?:feature|endpoint|handler)\b",
    r"\bintegrat(?:e|ion)\b",
    r"\bupdate\s+(?:the|this)\b",
    r"\bconfigure\b",
    r"\bsetup\b",
    r"\breview\b",
    r"\btest(?:ing)?\b",
]

LOW_COMPLEXITY_SIGNALS = [
    r"\bwhat\s+is\b",
    r"\bshow\s+me\b",
    r"\blist\b",
    r"\bhelp\b",
    r"\bstatus\b",
    r"\bweather\b",
    r"\btime\b",
    r"\bhello\b",
    r"\bhi\b",
    r"\bthanks?\b",
    r"\bremind\b",
]


@dataclass
class ComplexityAssessment:
    """Result of task complexity analysis."""
    score: float           # 0.0 (trivial) to 1.0 (very complex)
    tier: str              # recommended tier: xhigh / high / mid / low / fast
    reasons: List[str]     # human-readable reasons for the assessment
    word_count: int
    signal_matches: List[str]


@dataclass
class ModelChoice:
    """Selected model with reasoning."""
    model_id: str
    model_name: str        # human-readable name from --list-models
    tier: str
    reasons: List[str]     # why this model was chosen
    available_models: List[str]  # full list discovered


@dataclass
class SmartDelegateResult:
    """Full result of smart delegation."""
    success: bool
    response: Optional[str]
    model_choice: Optional[ModelChoice]
    complexity: Optional[ComplexityAssessment]
    error: Optional[str] = None
    announcement: Optional[str] = None  # user-facing explanation of the choice


# ── Model discovery ─────────────────────────────────────────────────────

# Cache models for 5 minutes to avoid repeated subprocess calls
_models_cache: Optional[Tuple[float, List[Dict[str, str]]]] = None
_MODELS_CACHE_TTL = 300  # seconds


def discover_models(cursor_agent_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Discover available models from cursor-agent --list-models.
    Returns list of {"id": "model-id", "name": "Human Name"}.
    """
    global _models_cache
    now = time.time()
    if _models_cache and (now - _models_cache[0]) < _MODELS_CACHE_TTL:
        return _models_cache[1]

    agent_path = cursor_agent_path or os.path.expanduser("~/.local/bin/cursor-agent")
    models: List[Dict[str, str]] = []
    try:
        result = subprocess.run(
            ["bash", agent_path, "--list-models"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # Parse lines like: "opus-4.6-thinking - Claude 4.6 Opus (Thinking)  (default)"
            for line in result.stdout.splitlines():
                line = line.strip()
                # Skip ANSI escape codes, empty lines, headers, tips
                line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
                if not line or line.startswith("Available") or line.startswith("Tip:") or line.startswith("Loading"):
                    continue
                match = re.match(r'^(\S+)\s+-\s+(.+)$', line)
                if match:
                    model_id = match.group(1).strip()
                    model_name = match.group(2).strip()
                    # Remove (default) / (current) markers from name
                    model_name = re.sub(r'\s*\((?:default|current)\)\s*', '', model_name).strip()
                    models.append({"id": model_id, "name": model_name})
    except Exception as e:
        logger.warning(f"Failed to discover models: {e}")

    if models:
        _models_cache = (now, models)
        logger.info(f"Discovered {len(models)} models")
    else:
        logger.warning("No models discovered, using fallback tier preferences")

    return models


def _get_model_name(model_id: str, models: List[Dict[str, str]]) -> str:
    """Get human-readable name for a model id."""
    for m in models:
        if m["id"] == model_id:
            return m["name"]
    return model_id


# ── Complexity assessment ───────────────────────────────────────────────

def assess_complexity(task: str) -> ComplexityAssessment:
    """Assess the complexity of a task and recommend a model tier."""
    task_lower = task.lower()
    words = task.split()
    word_count = len(words)

    high_matches = []
    mid_matches = []
    low_matches = []

    for pattern in HIGH_COMPLEXITY_SIGNALS:
        m = re.search(pattern, task_lower)
        if m:
            high_matches.append(m.group(0))

    for pattern in MID_COMPLEXITY_SIGNALS:
        m = re.search(pattern, task_lower)
        if m:
            mid_matches.append(m.group(0))

    for pattern in LOW_COMPLEXITY_SIGNALS:
        m = re.search(pattern, task_lower)
        if m:
            low_matches.append(m.group(0))

    # Score calculation
    score = 0.3  # baseline

    # High-complexity signals are strong
    score += min(len(high_matches) * 0.15, 0.45)

    # Mid-complexity signals contribute moderately
    score += min(len(mid_matches) * 0.08, 0.2)

    # Low-complexity signals pull score down
    score -= min(len(low_matches) * 0.1, 0.3)

    # Longer tasks tend to be more complex
    if word_count > 100:
        score += 0.15
    elif word_count > 50:
        score += 0.1
    elif word_count > 25:
        score += 0.05
    elif word_count < 10:
        score -= 0.1

    # Multiple sentences / multi-step indicators
    sentence_count = len(re.split(r'[.!?]+', task.strip()))
    if sentence_count > 4:
        score += 0.1
    elif sentence_count > 2:
        score += 0.05

    # Chained actions: "do X, then Y, add Z, and deploy W" — commas + conjunctions indicate multi-step
    action_verbs = re.findall(
        r'\b(?:implement|add|write|create|build|deploy|configure|setup|test|fix|update|refactor|migrate|research|analyze)\b',
        task_lower,
    )
    if len(action_verbs) >= 4:
        score += 0.2
    elif len(action_verbs) >= 3:
        score += 0.12
    elif len(action_verbs) >= 2:
        score += 0.05

    # Code blocks in the task (pasted code to analyze)
    if '```' in task or re.search(r'(?:def |class |function |import )', task):
        score += 0.1

    # Clamp
    score = max(0.0, min(1.0, score))

    # Map score to tier
    reasons = []
    all_matches = high_matches + mid_matches

    if score >= 0.75:
        tier = "xhigh"
        reasons.append(f"Very complex task (score {score:.2f})")
        if high_matches:
            reasons.append(f"Key signals: {', '.join(high_matches[:3])}")
        reasons.append("Needs deep reasoning model for best results")
    elif score >= 0.55:
        tier = "high"
        reasons.append(f"Complex task (score {score:.2f})")
        if high_matches:
            reasons.append(f"Complexity indicators: {', '.join(high_matches[:3])}")
        reasons.append("Strong model recommended for accuracy")
    elif score >= 0.35:
        tier = "mid"
        reasons.append(f"Moderate complexity (score {score:.2f})")
        if mid_matches:
            reasons.append(f"Task involves: {', '.join(mid_matches[:3])}")
    elif score >= 0.2:
        tier = "low"
        reasons.append(f"Straightforward task (score {score:.2f})")
    else:
        tier = "fast"
        reasons.append(f"Simple task (score {score:.2f})")
        reasons.append("Fast model is sufficient")

    return ComplexityAssessment(
        score=score,
        tier=tier,
        reasons=reasons,
        word_count=word_count,
        signal_matches=all_matches,
    )


# ── Model selection ─────────────────────────────────────────────────────

def select_model(
    complexity: ComplexityAssessment,
    available_models: List[Dict[str, str]],
    exclude_model: Optional[str] = None,
    preferred_tier: Optional[str] = None,
) -> ModelChoice:
    """Select the optimal model for the given complexity from available models.
    
    Args:
        complexity: The complexity assessment.
        available_models: Models from discover_models().
        exclude_model: Model to exclude (e.g. the one already being used).
        preferred_tier: Override tier preference.
    """
    available_ids = {m["id"] for m in available_models}
    target_tier = preferred_tier or complexity.tier

    # Build ordered candidate list: target tier first, then adjacent tiers
    tier_order = sorted(MODEL_TIERS.keys(), key=lambda t: abs(TIER_RANK.get(t, 0) - TIER_RANK.get(target_tier, 3)), reverse=False)

    reasons = list(complexity.reasons)
    chosen_id = None
    chosen_tier = target_tier

    for tier in tier_order:
        for model_id in MODEL_TIERS.get(tier, []):
            if model_id in available_ids and model_id != exclude_model:
                chosen_id = model_id
                chosen_tier = tier
                if tier != target_tier:
                    reasons.append(f"Preferred tier '{target_tier}' not available; using '{tier}' tier")
                break
        if chosen_id:
            break

    # Fallback: pick any available model that isn't excluded
    if not chosen_id:
        for m in available_models:
            if m["id"] != exclude_model and m["id"] != "auto":
                chosen_id = m["id"]
                chosen_tier = _MODEL_TO_TIER.get(m["id"], "mid")
                reasons.append(f"Fallback: selected '{chosen_id}' as no tier-matched model was available")
                break

    if not chosen_id:
        # Absolute fallback
        chosen_id = "auto"
        chosen_tier = "mid"
        reasons.append("No specific model available; using 'auto'")

    model_name = _get_model_name(chosen_id, available_models)
    reasons.append(f"Selected: {model_name} ({chosen_id})")

    return ModelChoice(
        model_id=chosen_id,
        model_name=model_name,
        tier=chosen_tier,
        reasons=reasons,
        available_models=[m["id"] for m in available_models],
    )


# ── Announcement formatting ─────────────────────────────────────────────

def format_announcement(complexity: ComplexityAssessment, choice: ModelChoice) -> str:
    """Format a user-facing announcement explaining the delegation choice."""
    tier_emoji = {
        "xhigh": "🧠",
        "high": "💪",
        "mid": "⚡",
        "low": "✅",
        "fast": "⚡",
    }
    tier_label = {
        "xhigh": "Maximum Reasoning",
        "high": "High Capability",
        "mid": "Standard",
        "low": "Light",
        "fast": "Fast",
    }

    emoji = tier_emoji.get(choice.tier, "🤖")
    label = tier_label.get(choice.tier, choice.tier)

    lines = [
        f"{emoji} **Delegating to {choice.model_name}** [{label}]",
        "",
    ]

    # Explain why
    if complexity.score >= 0.55:
        lines.append(f"Task complexity: {'very ' if complexity.score >= 0.75 else ''}high (score {complexity.score:.0%})")
    elif complexity.score >= 0.35:
        lines.append(f"Task complexity: moderate (score {complexity.score:.0%})")
    else:
        lines.append(f"Task complexity: low (score {complexity.score:.0%})")

    if complexity.signal_matches:
        lines.append(f"Signals: {', '.join(complexity.signal_matches[:4])}")

    lines.append(f"Model: {choice.model_id}")
    lines.append("")
    lines.append("Sending clean context to the delegate agent...")

    return "\n".join(lines)


# ── Main tool class ─────────────────────────────────────────────────────

class SmartDelegateTool:
    """
    Smart delegation: analyze task complexity, pick the best available model,
    announce the choice, and run a sub-agent with clean context.
    Use for complex tasks that benefit from a stronger model and cleaner prompt.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, cursor_agent_path: Optional[str] = None):
        self.config = config or {}
        self._cursor_agent_path = (
            cursor_agent_path
            or self.config.get("cursor_agent_path")
            or (self.config.get("delegate") or {}).get("cursor_agent_path")
            or os.path.expanduser("~/.local/bin/cursor-agent")
        )

    async def execute(
        self,
        task: str,
        force_tier: Optional[str] = None,
        force_model: Optional[str] = None,
        exclude_model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Delegate a task to the optimal model.

        Args:
            task: The task to delegate (clean description).
            force_tier: Force a specific tier (xhigh/high/mid/low/fast).
            force_model: Force a specific model id.
            exclude_model: Exclude a specific model (e.g. the one already in use).
            system_prompt: Optional system prompt for the sub-agent.
            timeout_seconds: Timeout for the sub-agent.

        Returns:
            Dict with keys: success, response, announcement, model_choice, complexity, error
        """
        if not task or not task.strip():
            return {"success": False, "error": "task is required", "response": None}

        task = task.strip()

        # 1. Discover available models
        models = discover_models(self._cursor_agent_path)
        if not models:
            # Fallback: still try with auto
            models = [{"id": "auto", "name": "Auto"}]

        # 2. Assess complexity
        complexity = assess_complexity(task)

        # 3. Select model
        if force_model:
            model_id = force_model
            model_name = _get_model_name(force_model, models)
            choice = ModelChoice(
                model_id=model_id,
                model_name=model_name,
                tier=_MODEL_TO_TIER.get(model_id, "mid"),
                reasons=[f"Model forced: {model_id}"],
                available_models=[m["id"] for m in models],
            )
        else:
            choice = select_model(
                complexity,
                models,
                exclude_model=exclude_model,
                preferred_tier=force_tier,
            )

        # 4. Format announcement
        announcement = format_announcement(complexity, choice)

        # 5. Build prompt with clean context
        prompt_parts = []
        if system_prompt:
            prompt_parts.append(f"System: {system_prompt}")
        prompt_parts.append(f"Task:\n{task}")
        prompt = "\n\n".join(prompt_parts)

        # 6. Run sub-agent
        default_timeout = (self.config.get("delegate") or {}).get("timeout_seconds", 3600)
        timeout = max(60, int(timeout_seconds or default_timeout))

        cmd = ["bash", self._cursor_agent_path, "--force"]
        if choice.model_id and choice.model_id != "auto":
            cmd.extend(["--model", choice.model_id])
        cmd.extend(["-p", prompt])

        env = os.environ.copy()
        # Pass MCP config so delegate can use tools
        mcp_path = self.config.get("mcp_config_path")
        if mcp_path:
            expanded = os.path.expanduser(str(mcp_path))
            if os.path.isfile(expanded):
                env["CURSOR_MCP_CONFIG_PATH"] = expanded

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.path.expanduser("~"),
                    env=env,
                ),
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Delegate timed out after {timeout}s",
                "response": None,
                "announcement": announcement,
                "model_choice": _choice_to_dict(choice),
                "complexity": _complexity_to_dict(complexity),
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"cursor-agent not found at {self._cursor_agent_path}",
                "response": None,
                "announcement": announcement,
                "model_choice": _choice_to_dict(choice),
                "complexity": _complexity_to_dict(complexity),
            }
        except Exception as e:
            logger.exception("Smart delegate failed")
            return {
                "success": False,
                "error": str(e),
                "response": None,
                "announcement": announcement,
                "model_choice": _choice_to_dict(choice),
                "complexity": _complexity_to_dict(complexity),
            }

        response_text = (result.stdout or "").strip()
        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr or f"Exit code {result.returncode}",
                "response": response_text or None,
                "announcement": announcement,
                "model_choice": _choice_to_dict(choice),
                "complexity": _complexity_to_dict(complexity),
            }

        return {
            "success": True,
            "response": response_text,
            "announcement": announcement,
            "model_choice": _choice_to_dict(choice),
            "complexity": _complexity_to_dict(complexity),
        }


def _choice_to_dict(choice: ModelChoice) -> Dict[str, Any]:
    return {
        "model_id": choice.model_id,
        "model_name": choice.model_name,
        "tier": choice.tier,
        "reasons": choice.reasons,
    }


def _complexity_to_dict(complexity: ComplexityAssessment) -> Dict[str, Any]:
    return {
        "score": complexity.score,
        "tier": complexity.tier,
        "reasons": complexity.reasons,
        "word_count": complexity.word_count,
        "signal_matches": complexity.signal_matches,
    }
