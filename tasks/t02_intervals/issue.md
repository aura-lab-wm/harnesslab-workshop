# Issue #7: touching intervals are not merged

`merge_intervals([(1, 3), (3, 5)])` returns `[(1, 3), (3, 5)]` but the documented
behaviour is that closed intervals sharing an endpoint merge into `[(1, 5)]`.
Unsorted input must also be handled. Fix `ranges/merge.py`.
