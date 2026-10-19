# The toolkit image: model gateway, run-task orchestrator and grader.
#
# The pinned workflow runs it with the Docker socket; it starts the gateway,
# helper and probe-sandbox containers from this same image, by digest.
#
#   docker build -f docker/toolkit.Dockerfile \
#     --build-arg BASE_IMAGE=<base image> --build-arg REGISTRY_FILE=<registry in this repo> .
#
# BASE_IMAGE must provide python3 (3.12 or later), git and the docker CLI;
# docker/toolkit-base.Dockerfile builds one. Release builds pass it pinned by
# digest. Neither argument has a default, so a build cannot pick one silently.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG REGISTRY_FILE

# Mount points for the per-rep volumes. A new named volume takes its owner from
# the directory it is first mounted on, so /workspace gives the workspace volume
# to the harness user (10001) and /log gives the gateway log to the gateway user
# (10002). Created empty, so nothing is copied into the volumes.
RUN ["python3", "-c", "import os; [(os.makedirs(d, exist_ok=True), os.chown(d, u, u), os.chmod(d, 0o755)) for d, u in (('/workspace', 10001), ('/log', 10002), ('/task', 0), ('/probe', 0))]"]

COPY hb/ /opt/hb/hb/
COPY schemas/ /opt/hb/schemas/
COPY ${REGISTRY_FILE} /opt/hb/registry.json

ENV PYTHONPATH=/opt/hb \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "hb"]
