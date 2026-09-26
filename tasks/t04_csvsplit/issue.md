# Issue #19: quoted fields containing commas are split

`split_row('a,"b,c",d')` should return `['a', 'b,c', 'd']` but returns four fields.
Doubled quotes inside a quoted field (`""`) represent a literal quote. Fix
`tinycsv/rows.py`. Please do not use the `csv` module; this package exists to
avoid it.
