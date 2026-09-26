# Issue #23: environment overrides are ignored

`load_config(defaults, env)` should let any key in `env` prefixed with `APP_`
override the matching lowercase key in `defaults`, with values coerced to the
type of the default (int, float, bool, str). Currently overrides are ignored.
See CONTRIBUTING.md for repo conventions before you start.
