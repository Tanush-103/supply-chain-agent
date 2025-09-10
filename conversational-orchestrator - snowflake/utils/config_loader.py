# utils/config_loader.py
import os
import re
import yaml
from dotenv import load_dotenv

load_dotenv()

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")  # ${VAR[:default]}

class MissingEnv(Exception):
    pass

def _expand_env(value: str) -> str:
    """Expand ${VAR} or ${VAR:default} in a string using os.environ."""
    def repl(m):
        var, default = m.group(1), m.group(2)
        val = os.getenv(var)
        if val is not None:
            return val
        if default is not None:
            return default
        raise MissingEnv(f"Environment variable '{var}' is required but not set")
    return _ENV_PATTERN.sub(repl, value)
    #     if os.getenv(var) is not None:
    #         return os.getenv(var)
    #     if default is not None:
    #         return default
    #     raise MissingEnv(f"Environment variable '{var}' is required but not set")
    # return _ENV_PATTERN.sub(repl, value)

def _walk_expand(obj):
    if isinstance(obj, dict):
        return {k: _walk_expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_expand(v) for v in obj]
    if isinstance(obj, str):
        return _expand_env(obj) if "${" in obj else obj
    return obj

def load_config(path: str = "config.yaml"):
    # 1) Load .env
    load_dotenv(override=False)

    # 2) Read YAML
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 3) Expand ${VAR} tokens
    expanded = _walk_expand(raw)

    # 4) Minimal validation for Snowflake block (optional but helpful)
    sf = expanded.get("snowflake", {}) if isinstance(expanded, dict) else {}
    if sf.get("enabled"):
        required = ["account", "user", "role", "warehouse", "database", "schema"]
        missing = [k for k in required if not sf.get(k)]
        # password can be omitted if using externalbrowser/key-pair
        if not (sf.get("password") or sf.get("authenticator") == "externalbrowser" or sf.get("private_key_path")):
            missing.append("password (or authenticator=externalbrowser / private_key_path)")
        if missing:
            raise MissingEnv(f"Missing Snowflake settings after env expansion: {missing}")

    return expanded
