# Issue #301: remove the deprecated handler

We still ship the deprecated handler in `api/handlers.py`. Remove it and its
registration so it no longer appears in `registry()`. Everything else must keep
working.
