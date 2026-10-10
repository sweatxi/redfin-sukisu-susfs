# check=skip=FromPlatformFlagConstDisallowed
FROM --platform=linux/amd64 ubuntu:24.04@sha256:b3cc40b72b93588182b5410f723c7aaf142363311c2aa993d8a453ddcbb3ae15

ENV DEBIAN_FRONTEND=noninteractive
# Kernel compiler and build utilities come from pinned submodules staged under /stock.
# Pin all APT sources to the snapshot first, then drop legacy live sources so every
# install resolves against the same (2026-09-19) archive state.
RUN printf 'APT::Snapshot "20260919T000000Z";
'       > /etc/apt/apt.conf.d/50snapshot &&     rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true &&     apt-get update && apt-get install -y --no-install-recommends       ca-certificates libssl3t64 openssl &&     rm -rf /var/lib/apt/lists/* &&     apt-get update && apt-get install -y --no-install-recommends     bc bison build-essential ca-certificates cpio flex git kmod     libelf-dev libssl-dev libncurses-dev lz4 python3 rsync unzip     file openssl xz-utils zip curl &&     rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENTRYPOINT ["/workspace/scripts/build-redfin-sukisu.sh"]
