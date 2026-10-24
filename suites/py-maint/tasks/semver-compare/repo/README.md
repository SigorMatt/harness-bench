# semverlite

Parse and order [semantic versions](https://semver.org) with the standard
library only.

```python
from semverlite import parse, compare, max_version

parse("1.4.0-rc.1+build.7")        # Version(major=1, minor=4, patch=0, ...)
compare("1.4.0-rc.1", "1.4.0")     # -1
max_version(["1.2.0", "1.10.0"])   # Version 1.10.0
```

## Precedence

`compare()`, `max_version()` and `sort_versions()` order versions by the
precedence of Semantic Versioning 2.0.0 (§11):

1. Major, minor and patch are compared as numbers, in that order.
2. When they are equal, a version with a pre-release has lower precedence than
   the version without one: `1.0.0-alpha < 1.0.0`.
3. Two pre-releases of the same version are compared identifier by identifier,
   from left to right, until one differs:
   - identifiers of digits only are compared as numbers;
   - other identifiers are compared as strings, in ASCII order;
   - an identifier of digits only has lower precedence than any other.

   If every identifier compared is equal, the version with more identifiers
   has higher precedence.
4. Build metadata (after `+`) is ignored: `1.0.0+a` and `1.0.0+b` have the same
   precedence.

For example: `1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta <
1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0`.
