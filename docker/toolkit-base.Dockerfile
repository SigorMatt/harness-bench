# Base for the toolkit image: Python, git (diffs and patches are computed with
# git outside any repository) and the docker CLI (the toolkit starts sibling
# containers through the host's daemon).
#
#   docker build -f docker/toolkit-base.Dockerfile docker
#
# Both images are pinned by digest (their multi-platform index), so CI and the
# release workflow build on exactly the same base; change them by pull request.
# The build is not bit-for-bit reproducible (git comes from Debian's archive at
# build time, and images record build timestamps): what a release pins is the
# toolkit image it built and attested, by digest (spec §6.3).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG DOCKER_CLI_IMAGE=docker:28-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d
FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
