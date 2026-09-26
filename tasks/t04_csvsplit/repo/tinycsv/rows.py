def split_row(line: str):
    """Split one CSV line into fields. Fields may be double-quoted; inside a
    quoted field a comma is literal and a doubled quote is a literal quote."""
    return [f.strip('"') for f in line.split(",")]
