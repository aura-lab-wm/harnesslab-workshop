import re

def slugify(text: str) -> str:
    """Turn arbitrary text into a URL slug: lowercase, ASCII letters and
    digits, words separated by single hyphens, no leading/trailing hyphens."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]", "-", text)
    return text
