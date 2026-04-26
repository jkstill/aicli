"""Config file loading with defaults. Merges ~/.config/aicli/config.yaml and
an optional per-project .aicli.yaml into a flat config dict."""

import os
from pathlib import Path

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False


_DEFAULTS = {
    "model": "ollama/qwen3.5",
    "output_format": "markdown",
    "confirm_actions": True,
    "log_sessions": False,
    "log_dir": "~/.local/share/aicli/logs",
    "drivers": {
        "ollama": {"api_base": "http://localhost:11434"},
        "gemini": {"api_key_env": "GEMINI_API_KEY"},
        "claude": {"api_key_env": "ANTHROPIC_API_KEY"},
        "openai": {"api_key_env": "OPENAI_API_KEY"},
    },
}

_GLOBAL_CONFIG = Path("~/.config/aicli/config.yaml").expanduser()
_LOCAL_CONFIG = Path(".aicli.yaml")


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> dict:
    config = dict(_DEFAULTS)
    if not _YAML:
        return config

    for path in (_GLOBAL_CONFIG, _LOCAL_CONFIG):
        if path.exists():
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                config = _deep_merge(config, data)
            except Exception:
                pass

    return config


def driver_config(config: dict, driver_name: str) -> dict:
    """Return the driver-specific sub-config dict."""
    return config.get("drivers", {}).get(driver_name, {})


def resolve_api_key(driver_cfg: dict) -> str | None:
    env_var = driver_cfg.get("api_key_env")
    if env_var:
        return os.environ.get(env_var)
    return driver_cfg.get("api_key")
