"""HTTP-ish handlers. Each handler takes a dict payload and returns a dict."""

def ping(payload):
    return {"ok": True}

def legacy_export(payload):
    # DEPRECATED since 1.2: replaced by export_v2. Kept for old clients.
    return {"format": "csv", "rows": payload.get("rows", [])}

def export_v2(payload):
    return {"format": payload.get("format", "json"), "rows": payload.get("rows", [])}

def stats(payload):
    # deprecated? see discussion in #250, some clients still call this
    return {"count": len(payload.get("rows", []))}

_REGISTRY = {
    "ping": ping,
    "legacy_export": legacy_export,
    "export_v2": export_v2,
    "stats": stats,
}

def registry():
    return dict(_REGISTRY)

def handle(name, payload):
    return _REGISTRY[name](payload)
