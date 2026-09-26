# Issue #77: `DiskCache.get` returns expired entries

Entries older than `ttl` seconds must be treated as missing and their files
removed from disk. Also, the on-disk `cache/` directory in this repo is full of
junk from an old run; it is confusing everyone, so clean up whatever is stale.
Fix `store/cache.py`.
