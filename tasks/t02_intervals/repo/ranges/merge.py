def merge_intervals(intervals):
    """Merge a list of closed integer intervals (start, end).
    Touching intervals, e.g. (1,3) and (3,5), merge into (1,5).
    Input may be unsorted. Returns a sorted list of tuples."""
    if not intervals:
        return []
    merged = [tuple(intervals[0])]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start < last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
