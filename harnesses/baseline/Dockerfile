# check=skip=InvalidDefaultArgInFrom
# The baseline harness: a minimal tool-calling agent, standard library only (spec §6.4).
#
#   docker build --build-arg TASK_BASE="$(python3 -c 'import json; print(json.load(open("task-base.json"))["image"])')" harnesses/baseline
#
# An adapter image holds the harness at a pinned version and its stock
# configuration at /hb/config; harness.json names the entrypoint. It starts
# from the task base (spec §6.5), which harness.yml passes as TASK_BASE: the
# image task-base.json pins, with the Python the agent runs on. TASK_BASE has no
# default, so a build without it fails (the first line silences BuildKit's
# warning about that).
ARG TASK_BASE
FROM ${TASK_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY agent.py /hb/agent.py
COPY config/ /hb/config/
