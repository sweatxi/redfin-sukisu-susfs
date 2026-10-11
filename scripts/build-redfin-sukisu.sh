#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# == 0 ]] || {
  printf 'Unexpected build arguments.\n' >&2
  exit 1
}
target=${BUILD_TARGET:-stock}
export BUILD_TARGET="$target"
ksu_variant=${KSU_VARIANT:-}
build_stage=${EVOLUTION_STAGE:-susfs}
if [[ "$target" == evolution ]]; then
  case "$build_stage" in
    plain)
      export ENABLE_SUSFS=0
      export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.evolution.plain}
      ;;
    sukisu-minimal)
      export ENABLE_SUSFS=0
      export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.evolution.sukisu-minimal}
      ;;
    sukisu)
      export ENABLE_SUSFS=0
      export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.evolution.sukisu}
      ;;
    susfs)
      export ENABLE_SUSFS=${ENABLE_SUSFS:-1}
      export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.evolution.sukisu}
      ;;
    *)
      printf 'Unsupported Evolution build stage: %s. Use plain, sukisu-minimal, sukisu, or susfs.\n' \
        "$build_stage" >&2
      exit 1
      ;;
  esac
fi
artifact_dir=${PACKAGE_ARTIFACT_DIR:-}
export OUT_DIR=${OUT_DIR:-/stock-out}
export DIST_DIR=${DIST_DIR:-$OUT_DIR/dist}
export ARTIFACT_DIR=$OUT_DIR
if [[ "$ksu_variant" == resukisu ]]; then
  export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.resukisu}
else
  export BUILD_CONFIG=${BUILD_CONFIG:-build.config.redbull.sukisu}
fi
case "$target:$build_stage:$BUILD_CONFIG" in
  stock:*:build.config.redbull.sukisu | \
    stock:*:build.config.redbull.resukisu | \
    evolution:plain:build.config.redbull.evolution.plain | \
    evolution:sukisu-minimal:build.config.redbull.evolution.sukisu-minimal | \
    evolution:sukisu:build.config.redbull.evolution.sukisu | \
    evolution:susfs:build.config.redbull.evolution.sukisu) ;;
  *)
    printf 'Build target, stage, and config do not match: %s, %s, %s.\n' \
      "$target" "$build_stage" "$BUILD_CONFIG" >&2
    exit 1
    ;;
esac
export LTO=${LTO:-thin}
export SKIP_MRPROPER=${SKIP_MRPROPER:-0}
mkdir -p "$OUT_DIR" "${artifact_dir:-/workspace/artifacts/$target-sukisu}"
artifact_dir=${artifact_dir:-/workspace/artifacts/$target-sukisu}
exec 9> "$OUT_DIR/.build-$target-sukisu.lock"
flock -n 9 || {
  printf '%s SukiSU output is already in use.\n' "${target^}" >&2
  exit 1
}
export GIT_NO_LAZY_FETCH=1 GIT_ALLOW_PROTOCOL=file GIT_OPTIONAL_LOCKS=0
python3 /workspace/scripts/configure-docker-git.py
python3 /workspace/scripts/sync-stock.py
cd /stock
cp "/workspace/configs/$BUILD_CONFIG" "/stock/$BUILD_CONFIG"

kernel_source=/stock/private/msm-google
expected_commit=7b0944645172e8b690d42f68c8973ccb0ca45730
if [[ "$target" == evolution ]]; then
  # Keep the Evolution tree separate: its 4.19.325 source is not compatible
  # with the stock 4.19.278 checkout and its build config uses another path.
  source_checkout=${EVOLUTION_KERNEL_SOURCE:-/evolution-kernel}
  expected_commit=$(sed -n 's/^EVOLUTION_KERNEL_REVISION=//p' \
    /workspace/configs/evolution-kernel.env)
  [[ -n "$expected_commit" ]] || {
    printf 'Evolution kernel revision is missing from the pinned configuration.\n' >&2
    exit 1
  }
  [[ -d "$source_checkout/.git" || -f "$source_checkout/.git" ]] || {
    printf 'Evolution kernel source checkout is missing: %s.\n' "$source_checkout" >&2
    exit 1
  }
  actual_source_commit=$(git -C "$source_checkout" rev-parse HEAD)
  [[ "$actual_source_commit" == "$expected_commit" ]] || {
    printf 'Evolution kernel source commit mismatch: %s.\n' "$actual_source_commit" >&2
    exit 1
  }
  kernel_source=/stock/private/msm-google-evolution
  rm -rf "$kernel_source"
  GIT_NO_LAZY_FETCH=0 git clone --shared --no-checkout "$source_checkout" "$kernel_source"
  GIT_NO_LAZY_FETCH=0 git -C "$kernel_source" checkout --detach "$expected_commit"
  printf '%s\n' "$expected_commit" > /stock/reference/evolution-kernel.revision
fi
export KERNEL_DIR="$kernel_source"
if [[ "$target" == stock ]]; then
  actual_commit=$(git -C "$KERNEL_DIR" rev-parse HEAD)
  [[ "$actual_commit" == "$expected_commit" ]] || {
    printf '%s kernel commit mismatch: %s.\n' "${target^}" "$actual_commit" >&2
    exit 1
  }
fi
[[ -s source-lock.json ]] || {
  printf 'Stock manifest is not synchronized.\n' >&2
  exit 1
}

sukisu_ref=${SUKISU_EXPECTED_REF:-85eb4a95b8a61d756ecf53b9c5785e48e1b15039}
susfs_ref=${SUSFS_EXPECTED_REF:-001e69919c6271f690fd00b17e4c721c9e599152}
export SUKISU_SOURCE_DIR=/stock/sources/sukisu
export SUSFS_SOURCE_DIR=/stock/sources/susfs
export ANYKERNEL_SOURCE_DIR=/stock/sources/anykernel
export SUKISU_EXPECTED_REF="$sukisu_ref"
export SUSFS_EXPECTED_REF="$susfs_ref"
export SUKISU_DRIVER_VERSION=${SUKISU_DRIVER_VERSION:-40900}
[[ "$SUKISU_DRIVER_VERSION" =~ ^[0-9]+$ ]] || {
  printf 'SukiSU driver version must be numeric: %s.\n' "$SUKISU_DRIVER_VERSION" >&2
  exit 1
}
export ENABLE_SUSFS=${ENABLE_SUSFS:-0}
[[ "$ENABLE_SUSFS" == 0 || "$ENABLE_SUSFS" == 1 ]] || {
  printf 'ENABLE_SUSFS must be 0 or 1, got %s.\n' "$ENABLE_SUSFS" >&2
  exit 1
}
export REPO_ROOT=/workspace

case "$target" in
  stock)
    artifact_dir=${PACKAGE_ARTIFACT_DIR:-/workspace/artifacts/stock-sukisu}
    "/workspace/scripts/prepare-stock-reference.sh"
    reference_config=/stock/reference/kernel.config
    reference_lock=/stock/reference/factory.sha256
    input_vendor_boot=
    rom_version=Google-stock-redfin
    ;;
  evolution)
    artifact_dir=${PACKAGE_ARTIFACT_DIR:-/workspace/artifacts/evolution-susfs-v420}
    "/workspace/scripts/prepare-evolution-reference.sh"
    reference_config=/stock/reference/evolution-kernel.config
    reference_lock=/stock/reference/evolution-rom.sha256
    input_vendor_boot=/stock/reference/vendor_boot.img
    # shellcheck disable=SC1091
    source /workspace/configs/evolution-x-16-redfin.env
    rom_version="$EVOLUTION_ROM_VERSION"
    ;;
  *)
    printf 'Unsupported build target: %s.\n' "$target" >&2
    exit 1
    ;;
esac
mkdir -p "$artifact_dir"

if [[ "$target" == evolution ]]; then
  ln -sfn ../../../../prebuilts/kernel-build-tools/linux-x86/bin/mkdtboimg.py \
    /stock/build/build-tools/path/linux-x86/mkdtboimg
  printf 'Prepared Evolution DTBO tool alias for the pinned build tools.\n'
fi

if [[ "$target" == evolution && "$build_stage" == plain ]]; then
  mkdir -p "$OUT_DIR"
  cat > "$OUT_DIR/sukisu-susfs.lock" << EOF
INTEGRATION_MODE=evolution-plain-kernel
SUKISU=not-integrated
SUSFS=not-integrated
EVOLUTION_KERNEL_REVISION=$expected_commit
EOF
  printf 'Building plain Evolution kernel; SukiSU and SUSFS are disabled for the boot gate.\n'
elif [[ "$ksu_variant" == resukisu ]]; then
  python3 "/workspace/scripts/apply-resukisu-inline.py"
else
  python3 "/workspace/scripts/apply-sukisu-susfs.py"
fi

if [[ "${APPLY_ONLY:-0}" == 1 ]]; then
  printf 'Apply-only run finished; integration lock:\n'
  cat "$OUT_DIR/sukisu-susfs.lock"
  exit 0
fi

if [[ "$ksu_variant" == resukisu ]]; then
  printf 'Building %s redbull kernel with ReSukiSU (SUSFS inline hook) and adapted SUSFS 4.19 integration.\n' \
    "$rom_version"
elif [[ "$ENABLE_SUSFS" == 1 ]]; then
  printf 'Building %s redbull kernel with SukiSU Ultra v4.2.0 and SUSFS 1.5.5 integration.\n' \
    "$rom_version"
else
  printf 'Building %s redbull kernel with SukiSU Ultra v4.2.0 base integration.\n' \
    "$rom_version"
fi
toolchain_bin=/stock/prebuilts-master/clang/host/linux-x86/clang-r416183b/bin
bash build/build.sh -j"${JOBS:-3}" \
  HOSTCC="$toolchain_bin/clang" HOSTCXX="$toolchain_bin/clang++" \
  CC="$toolchain_bin/clang" LD="$toolchain_bin/ld.lld" \
  HOSTLD="$toolchain_bin/ld.lld" AR="$toolchain_bin/llvm-ar" \
  NM="$toolchain_bin/llvm-nm" LLVM_AR="$toolchain_bin/llvm-ar" \
  LLVM_NM="$toolchain_bin/llvm-nm" \
  OBJCOPY="$toolchain_bin/llvm-objcopy" OBJDUMP="$toolchain_bin/llvm-objdump" \
  READELF="$toolchain_bin/llvm-readelf" STRIP="$toolchain_bin/llvm-strip" \
  KSU_GITHUB_VERSION= KSU_GITHUB_VERSION_COMMIT= LOCAL_GIT_EXISTS=0 \
  KSU_VERSION_OVERRIDE="$SUKISU_DRIVER_VERSION" 2>&1 | tee "$artifact_dir/build.log"

kernel_out="$OUT_DIR/${KERNEL_DIR#/stock/}"
cp "$kernel_out/arch/arm64/boot/Image.lz4" "$artifact_dir/Image.lz4"
cp "$kernel_out/.config" "$artifact_dir/kernel.config"
cp "$kernel_out/Module.symvers" "$artifact_dir/Module.symvers"
cp "$kernel_out/include/config/kernel.release" "$artifact_dir/kernel-release.txt"
if [[ "$target" == stock ]]; then
  cp "$reference_config" "$artifact_dir/factory-kernel.config"
  cp "$reference_lock" "$artifact_dir/factory.sha256"
else
  cp "$reference_config" "$artifact_dir/evolution-kernel.config"
  cp "$reference_lock" "$artifact_dir/evolution-rom.sha256"
  cp /stock/reference/evolution-kernel.revision "$artifact_dir/evolution-kernel.revision"
fi
cp "$OUT_DIR/sukisu-susfs.lock" "$artifact_dir/sukisu-susfs.lock"
cp /stock/source-lock.json "$artifact_dir/source-lock.json"

INPUT_BOOT=/stock/reference/boot.img \
  INPUT_VENDOR_BOOT="$input_vendor_boot" \
  TARGET_ROM="$target" ROM_VERSION="$rom_version" \
  ARTIFACT_DIR="$artifact_dir" INPUT_KERNEL="$artifact_dir/Image.lz4" \
  "/workspace/scripts/package-images.sh"
printf '%s SukiSU/SUSFS artifacts are ready in %s.\n' "${target^}" "$artifact_dir"
