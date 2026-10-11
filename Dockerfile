# check=skip=FromPlatformFlagConstDisallowed
FROM --platform=linux/amd64 ubuntu:24.04@sha256:b3cc40b72b93588182b5410f723c7aaf142363311c2aa993d8a453ddcbb3ae15

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3t64 openssl && rm -rf /var/lib/apt/lists/* && apt-get update && apt-get install -y --no-install-recommends bc bison build-essential ca-certificates cpio flex git kmod libelf-dev libssl-dev libncurses-dev lz4 patch python3 rsync unzip file openssl xz-utils zip curl && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENTRYPOINT ["/workspace/scripts/build-redfin-sukisu.sh"]
