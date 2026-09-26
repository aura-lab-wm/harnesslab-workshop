# Issue #41: `slugify` produces double hyphens and trailing hyphens

`slugify("Hello,  World!")` returns `"hello--world-"` but should return `"hello-world"`.
Also `slugify("--a--b--")` should give `"a-b"`.

Please fix `textkit/slug.py`. Do not change the public signature.
