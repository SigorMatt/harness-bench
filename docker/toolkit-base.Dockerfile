# Base for the toolkit image: Python, git (diffs and patches are computed with
# git outside any repository) and the docker CLI (the toolkit starts sibling
# containers through the host's daemon).
#
#   docker build -f docker/toolkit-base.Dockerfile \
#     --build-arg PYTHON_IMAGE=<python image> --build-arg DOCKER_CLI_IMAGE=<docker cli image> docker
#
# The defaults are tags so that CI can build it; release builds pass both
# images pinned by digest, and the workflow pins the resulting toolkit image by
# digest (spec I2).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG DOCKER_CLI_IMAGE=docker:28-cli
FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
