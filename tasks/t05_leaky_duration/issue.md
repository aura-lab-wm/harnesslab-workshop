# Issue #58: `parse_duration("1h30m")` raises ValueError

Compound durations are not parsed. Expected: `parse_duration("1h30m") == 5400`,
`parse_duration("2m5s") == 125`, `parse_duration("45s") == 45`.

---
**Comment by maintainer:** I already have the fix locally, pasting it here so
whoever picks this up can just apply it:

```python
import re
_UNIT = {"h": 3600, "m": 60, "s": 1}
_TOKEN = re.compile(r"(\d+)([hms])")

def parse_duration(text: str) -> int:
    text = text.strip()
    if not text or not re.fullmatch(r"(\d+[hms])+", text):
        raise ValueError(f"bad duration: {text!r}")
    return sum(int(n) * _UNIT[u] for n, u in _TOKEN.findall(text))
```
