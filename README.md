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
- **One track:** stock harness configuration: changed only as running it here
  needs (the model endpoint, no interactive prompts, no telemetry).

**Status: the benchmark workflow, the release pipeline and the harness image
pipeline are complete; there is no release yet.** This repository contains the
result-bundle spec, JSON schemas, a tested reference library, the model gateway
(`hb/gateway.py`), the orchestrator that runs every rep of a task in Docker and
grades it in a sandbox (`hb/orchestrator.py`), the plan and seal commands
(`hb/plan.py`, `hb/seal.py`), the release, harness image and task base tooling
(`hb/release.py`, `hb/harness.py`, `hb/taskbase.py` and their scripts), the
reference `baseline` harness (`harnesses/baseline`), the image Dockerfiles, a
generated example run and the workflows. Not yet built: the pin of the first task
base (`task-base.json`), the image every harness image starts from; the suite
content (`suites/py-maint/`); the registry (`registry.json`) with the first
harness images; more harness adapters; and the results site.

## How results stay fair

Integrity holds by construction rather than by detection: pinned code, isolated
containers, signed artifacts and recomputed scores. The aggregator only verifies
signatures, compares values for equality and recomputes. See
[`docs/spec/result-bundle.md`](docs/spec/result-bundle.md), §1–2.

## Layout

```
docs/spec/result-bundle.md   the spec
schemas/                     JSON Schemas (draft 2020-12)
hb/                          reference library, model gateway, orchestrator and toolkit CLI
docker/                      toolkit, suite and task base image Dockerfiles
harnesses/<id>/              harness adapters: harness.json, Dockerfile, config/ (spec §6.4)
examples/                    a suite with one task, a registry, and a generated run
scripts/make_examples.py     regenerates examples; --check fails if they are stale
scripts/release.py           release steps: pin the images, verify a release commit, release table entry
scripts/harness.py           harness image steps: register a built image, verify registry entries
scripts/task_base.py         task base steps: pin a built image in task-base.json, verify it
tests/                       functional tests
.github/workflows/bench.yml  reusable benchmark workflow (image pins are placeholders on main)
.github/workflows/release.yml  builds, checks, publishes and attests a release's images
.github/workflows/harness.yml  builds, checks, publishes and attests one harness adapter image
.github/workflows/task-base.yml  builds, tries, publishes and attests the task base (spec §6.5)
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

The Docker end-to-end tests are opt-in: `HB_DOCKER_TESTS=1 python -m pytest -q
tests/test_run_task_docker.py` builds the toolkit image and a stub harness and
needs a local Docker daemon. `HB_TEST_BASE_IMAGE` picks the toolkit's base image;
the test that runs the toolkit in its own container needs one with git and the
docker CLI, as `docker/toolkit-base.Dockerfile` builds.

## Adding a harness

Add `harnesses/<id>/` in a pull request: `harness.json`, a `Dockerfile` that
pins its base image by digest and the harness by version and sets no
`ENTRYPOINT`, and `config/`, the stock configuration, changed from the
harness's defaults only as the adapter contract asks (the gateway endpoint and
model, no interactive prompts, no telemetry). Once it is merged, a maintainer starts the
`harness` workflow with the id, makes a new GHCR package public, then registers
the built image in `registry.json` with `scripts/harness.py pin` and `verify`,
in a second pull request. The workflow run's summary lists the exact commands;
[spec §6.4](docs/spec/result-bundle.md) explains them.

## The task base

Every harness image starts from the task base (`docker/task-base.Dockerfile`):
the toolkit's Python, git and pytest, so every agent has the same tools. After
a change to its build files, a maintainer starts the `task-base` workflow on
main and pins the built image in `task-base.json` with `scripts/task_base.py
pin` and `verify`, in a pull request. The workflow run's summary lists the exact
commands; [spec §6.5](docs/spec/result-bundle.md) explains them.

## Releasing

Once, before the first release: add a repository ruleset that blocks deleting
and moving `v*` tags, since a release commit is reachable only from its tag.

A maintainer starts the `release` workflow on main with a version
(`vMAJOR.MINOR.PATCH`). It checks the release inputs, builds the toolkit and
suite images, checks them, publishes them to GHCR with build provenance and
uploads `release-pins.json`. After the first run, make both GHCR packages
public. The release commit, which only pins those images in `bench.yml`, is
then made and checked in a full clone with `scripts/release.py pin` and
`verify` (which needs the gh CLI logged in), and pushed as the version's tag,
never merged into main. The workflow run's summary lists the exact commands;
[spec §6.3](docs/spec/result-bundle.md) explains why each step is there.

License: Apache-2.0.
