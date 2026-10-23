# The task base (spec §6.5): the image every harness image starts from, so
# every agent has the same Python, git and pytest whatever its harness.
#
#   docker build -f docker/task-base.Dockerfile docker
#
# - Python 3.12 from the toolkit's own pinned image, the interpreter probes run on.
# - git and patch from Debian's archive, with a system-wide identity so an agent
#   can commit, and main as the default branch.
# - pytest from docker/task-base-requirements.txt, every wheel pinned by hash;
#   the image keeps the file at /opt/hb/task-base-requirements.txt.
# - An account for the harness container's user (10001, home /tmp), so tools
#   that look up the current user find one.
#
# The build is not bit-for-bit reproducible (Debian's archive changes), so what
# is pinned is the image task-base.yml built and attested, by digest, in
# task-base.json.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE}
COPY task-base-requirements.txt /opt/hb/task-base-requirements.txt
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git patch \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pip install --no-cache-dir --disable-pip-version-check --no-input --root-user-action=ignore \
      --require-hashes --only-binary :all: --requirement /opt/hb/task-base-requirements.txt \
 && git config --system user.name "harness-bench agent" \
 && git config --system user.email "agent@harness-bench.invalid" \
 && git config --system init.defaultBranch main \
 && groupadd --gid 10001 hb \
 && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --no-log-init --shell /usr/sbin/nologin hb
