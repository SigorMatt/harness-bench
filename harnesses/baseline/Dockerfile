# The baseline harness: a minimal tool-calling agent, standard library only (spec §6.4).
#
#   docker build harnesses/baseline
#
# An adapter image holds the harness at a pinned version and its stock
# configuration at /hb/config; harness.json names the entrypoint. The base is
# the same Python image the toolkit uses, pinned by digest.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY agent.py /hb/agent.py
COPY config/ /hb/config/
