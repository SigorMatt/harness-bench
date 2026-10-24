`import plugboard` fails on Python 3.12, which we now run on: some of the
standard library modules and names it uses no longer exist.

Make plugboard import and work on Python 3.12 without changing its behaviour.
Use only the standard library: plugboard must not depend on any other package.
