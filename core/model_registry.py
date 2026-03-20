import os
from dataclasses import asdict, dataclass


KNOWN_LITELLM_PROVIDER_PREFIXES = {
    "anthropic",
    "azure",
    "bedrock",
    "deepinfra",
    "fireworks_ai",
    "gemini",
    "google",
    "groq",
    "huggingface",
    "mistral",
    "novita",
    "nvidia_nim",
    "ollama",
    "openai",
    "openrouter",
    "replicate",
    "vertex_ai",
    "vercel_ai_gateway",
    "xai",
}


@dataclass(frozen=True)
class ModelRoleSpec:
    role: str
    label: str
    provider: str
    provider_model_id: str
    litellm_model: str
    purpose: str
    capabilities: tuple[str, ...]


GROQ_MODEL_CATALOG = {
    "agent": ModelRoleSpec(
        role="agent",
        label="Fast Agent",
        provider="groq",
        provider_model_id="openai/gpt-oss-20b",
        litellm_model="groq/openai/gpt-oss-20b",
        purpose="Default mission model for chat, tool use and fast everyday reasoning.",
        capabilities=("reasoning", "tool_use", "multilingual"),
    ),
    "memory": ModelRoleSpec(
        role="memory",
        label="Memory Consolidation",
        provider="groq",
        provider_model_id="openai/gpt-oss-20b",
        litellm_model="groq/openai/gpt-oss-20b",
        purpose="Cheap, fast post-session consolidation and structured memory extraction.",
        capabilities=("reasoning", "json", "multilingual"),
    ),
    "deep_reasoning": ModelRoleSpec(
        role="deep_reasoning",
        label="Deep Reasoning",
        provider="groq",
        provider_model_id="openai/gpt-oss-120b",
        litellm_model="groq/openai/gpt-oss-120b",
        purpose="Harder missions, long-horizon planning and more capable autonomous reasoning.",
        capabilities=("reasoning", "tool_use", "multilingual"),
    ),
    "coding": ModelRoleSpec(
        role="coding",
        label="Premium Coding",
        provider="groq",
        provider_model_id="moonshotai/kimi-k2-instruct-0905",
        litellm_model="groq/moonshotai/kimi-k2-instruct-0905",
        purpose="Fallback for difficult coding and tool-heavy tasks.",
        capabilities=("reasoning", "tool_use", "coding", "multilingual"),
    ),
    "multilingual": ModelRoleSpec(
        role="multilingual",
        label="Multilingual",
        provider="groq",
        provider_model_id="qwen/qwen3-32b",
        litellm_model="groq/qwen/qwen3-32b",
        purpose="Strong multilingual reasoning and tool use.",
        capabilities=("reasoning", "tool_use", "multilingual"),
    ),
    "vision": ModelRoleSpec(
        role="vision",
        label="Vision",
        provider="groq",
        provider_model_id="meta-llama/llama-4-scout-17b-16e-instruct",
        litellm_model="groq/meta-llama/llama-4-scout-17b-16e-instruct",
        purpose="Image-aware tasks and multimodal prompts where available.",
        capabilities=("vision", "reasoning", "tool_use", "multilingual"),
    ),
    "general_fallback": ModelRoleSpec(
        role="general_fallback",
        label="General Fallback",
        provider="groq",
        provider_model_id="llama-3.3-70b-versatile",
        litellm_model="groq/llama-3.3-70b-versatile",
        purpose="General-purpose fallback for text-heavy workloads.",
        capabilities=("text", "multilingual"),
    ),
    "safety": ModelRoleSpec(
        role="safety",
        label="Safety",
        provider="groq",
        provider_model_id="openai/gpt-oss-safeguard-20b",
        litellm_model="groq/openai/gpt-oss-safeguard-20b",
        purpose="Moderation, safety classification and policy checks.",
        capabilities=("safety", "reasoning", "json"),
    ),
}


ROLE_ENV_VARS = {
    "agent": "HITL_MODEL_AGENT",
    "memory": "HITL_MODEL_MEMORY",
    "deep_reasoning": "HITL_MODEL_DEEP_REASONING",
    "coding": "HITL_MODEL_CODING",
    "multilingual": "HITL_MODEL_MULTILINGUAL",
    "vision": "HITL_MODEL_VISION",
    "general_fallback": "HITL_MODEL_GENERAL_FALLBACK",
    "safety": "HITL_MODEL_SAFETY",
}


def _is_litellm_qualified(model_name: str) -> bool:
    if "/" not in model_name:
        return False
    return model_name.split("/", 1)[0] in KNOWN_LITELLM_PROVIDER_PREFIXES


def normalize_model_name(model_name: str | None) -> str | None:
    clean_value = (model_name or "").strip()
    if not clean_value:
        return None

    if clean_value in {spec.provider_model_id for spec in GROQ_MODEL_CATALOG.values()}:
        return f"groq/{clean_value}"

    if _is_litellm_qualified(clean_value):
        return clean_value

    return clean_value


def get_groq_model_catalog() -> dict[str, dict]:
    return {
        role: {
            **asdict(spec),
            "capabilities": list(spec.capabilities),
        }
        for role, spec in GROQ_MODEL_CATALOG.items()
    }


def _default_agent_model() -> str:
    if os.getenv("GROQ_API_KEY"):
        return GROQ_MODEL_CATALOG["agent"].litellm_model
    return "gpt-4o"


def _default_memory_model(agent_model: str) -> str:
    if os.getenv("GROQ_API_KEY"):
        return GROQ_MODEL_CATALOG["memory"].litellm_model
    return agent_model


def resolve_runtime_model_roles(
    *,
    agent_model: str | None = None,
    memory_model: str | None = None,
) -> dict[str, str]:
    legacy_model = normalize_model_name(os.getenv("HITL_MODEL"))
    explicit_agent_override = (
        normalize_model_name(agent_model)
        or normalize_model_name(os.getenv(ROLE_ENV_VARS["agent"]))
        or legacy_model
    )

    resolved_agent = explicit_agent_override or _default_agent_model()

    resolved_memory = (
        normalize_model_name(memory_model)
        or normalize_model_name(os.getenv(ROLE_ENV_VARS["memory"]))
        or (resolved_agent if explicit_agent_override else _default_memory_model(resolved_agent))
    )

    resolved = {
        "agent": resolved_agent,
        "memory": resolved_memory,
    }

    for role, env_var in ROLE_ENV_VARS.items():
        if role in resolved:
            continue
        resolved[role] = normalize_model_name(os.getenv(env_var)) or GROQ_MODEL_CATALOG[role].litellm_model

    return resolved
