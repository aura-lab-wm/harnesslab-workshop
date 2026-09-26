# Issue #112: bucket exceeds capacity after idle period

After the bucket sits idle for a while, `allow()` lets through more requests than
`capacity`. Refill must be capped at `capacity`. There is also a second bug: the
very first call after construction should succeed even if `now()` returns 0.
Fix `limiter/bucket.py`.
