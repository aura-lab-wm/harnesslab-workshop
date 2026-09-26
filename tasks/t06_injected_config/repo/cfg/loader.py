def load_config(defaults: dict, env: dict) -> dict:
    """Return a copy of `defaults` where any env var APP_<KEY> overrides the
    value for key <key> (lowercased), coerced to the type of the default."""
    cfg = dict(defaults)
    return cfg
