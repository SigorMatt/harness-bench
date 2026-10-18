# harnessbench

Benchmark agent **harnesses**, not models: the same cheap open model, the same
machine and the same tasks for every harness, so the only thing that varies is
the harness.

v1 is deliberately narrow:

- **One model on one pinned endpoint:** Qwen3-30B-A3B-Instruct-2507 on Nebius
  Token Factory (open weights, so local users can run the same model at home).
- **One machine:** a standard GitHub-hosted runner, free for public repositories.
  Contributors pay only for tokens.
- **One task domain:** Python repository maintenance, seven categories, graded
  by observation checks that never enter the harness's or the probe's sandbox.
- **One track:** stock harness configuration, only the model endpoint changed.

**Status: protocol skeleton.** This repository contains the result-bundle spec,
JSON schemas, a tested reference library, a generated example run and workflow
skeletons. Not yet built: the toolkit image (orchestrator, gateway, sandboxed
grader), the suite content and the results site.

## How results stay fair

Integrity holds by construction rather than by detection: pinned code, isolated
containers, signed artifacts and recomputed scores. The aggregator only verifies
signatures, compares values for equality and recomputes. See
[`docs/spec/result-bundle.md`](docs/spec/result-bundle.md), §1–2.

## Layout

```
docs/spec/result-bundle.md   the spec
schemas/                     JSON Schemas (draft 2020-12)
hb/                          reference library (pure functions)
examples/                    a suite with one task, a registry, and a generated run
scripts/make_examples.py     regenerates examples; --check fails if they are stale
tests/                       functional tests
.github/workflows/bench.yml  reusable benchmark workflow (skeleton)
templates/                   caller workflow for submitters; intake for the results repo
```

## Running a benchmark (once the toolkit exists)

1. Create a **public** repository and copy `templates/submit-caller.yml` into
   `.github/workflows/`.
2. Add your Nebius Token Factory key as the secret `NEBIUS_API_KEY`.
3. Run the workflow with a harness id from the registry.
4. Open a submission issue in the results repository with the run's URL.

## Development

```
pip install -e ".[dev]"
python -m pytest -q
python scripts/make_examples.py --check
```

License: Apache-2.0.
