ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

WORKDIR /workspace
COPY . /workspace

# Replace BASE_IMAGE with the official competition base image and add only
# reproducible runtime dependencies before building a submission image.
# Do not copy model weights, data, credentials or generated artifacts.
CMD [bash]
