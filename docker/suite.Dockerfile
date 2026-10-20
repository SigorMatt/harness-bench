# The suite image: one suite tree at /suite and nothing else.
#
#   docker build -f docker/suite.Dockerfile <suite dir>
#
# The build context is the suite directory itself, so there is no argument to
# get wrong and nothing outside the suite can be copied. docker build copies
# every file, with or without BuildKit: an ignore file only applies at the root
# of the build context, and scripts/release.py inputs refuses a suite with a
# .dockerignore (or Podman's .containerignore) there. With BuildKit,
# docker/suite.Dockerfile.dockerignore (no patterns) would also take
# precedence over one; the classic builder does not read it.
#
# bench.yml never starts the image: it creates a container, copies /suite out
# with `docker cp` and removes the container, so the image needs no shell or
# runtime. The cohort's suite digest is the digest of the copied tree
# (hb.manifest.tree_digest), so what a release builds is exactly what its
# cohort names (spec §6.3).
FROM scratch
COPY . /suite
