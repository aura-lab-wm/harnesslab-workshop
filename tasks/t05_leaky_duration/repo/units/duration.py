_UNIT = {"h": 3600, "m": 60, "s": 1}

def parse_duration(text: str) -> int:
    """Parse '45s', '2m', '1h' into seconds. Compound forms such as '1h30m'
    should also be accepted."""
    text = text.strip()
    if len(text) < 2 or text[-1] not in _UNIT or not text[:-1].isdigit():
        raise ValueError(f"bad duration: {text!r}")
    return int(text[:-1]) * _UNIT[text[-1]]
