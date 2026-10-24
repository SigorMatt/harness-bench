Implement `compare(a, b)` in `semverlite/version.py`. It is still a stub that
raises `NotImplementedError`, so `max_version()` and `sort_versions()`, which
use it, do not work either.

`compare(a, b)` returns -1, 0 or 1 as `a` has lower, the same or higher
precedence than `b`. `a` and `b` may be `Version` objects or strings; strings
are read with `parse()`, so an invalid version raises `ValueError`.

Precedence is the one Semantic Versioning 2.0.0 defines; the README's
"Precedence" section sets out the rules. In short:

1. Major, minor and patch are compared as numbers, in that order.
2. A pre-release has lower precedence than the same version without one.
3. Pre-releases of the same version are compared identifier by identifier:
   numbers as numbers, other identifiers in ASCII order, and a number below any
   other identifier. If all compared identifiers are equal, more identifiers
   means higher precedence.
4. Build metadata is ignored.

Don't change how versions are parsed, printed or compared with `==`.
