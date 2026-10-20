# The suite image: one suite tree at /suite and nothing else.
#
#   docker build -f docker/suite.Dockerfile <suite dir>
#
# The build context is the suite directory itself, so there is no argument to
# get wrong and nothing outside the suite can be copied. The empty
# docker/suite.Dockerfile.dockerignore takes precedence over any .dockerignore
# inside the suite, so every file of the suite is copied.
#
# bench.yml never starts the image: it creates a container, copies /suite out
# with `docker cp` and removes the container, so the image needs no shell or
# runtime. The cohort's suite digest is the digest of the copied tree
# (hb.manifest.tree_digest), so what a release builds is exactly what its
# cohort names (spec §6.3).
FROM scratch
COPY . /suite
