#!/usr/bin/env python3
"""Apply the ReSukiSU (SUSFS inline-hook) integration to the pinned stock kernel.

This is the resukisu variant of apply-sukisu-susfs.py. The driver is ReSukiSU
built in `CONFIG_KSU_SUSFS` mode ("SUSFS Inline Hook") instead of SukiSU-Ultra
manual-hook mode:

 1. Clone the pinned ReSukiSU revision into the staged sources.
 2. Wire the driver into the kernel tree: copy `kernel/` to drivers/kernelsu and
    add the drivers/Kconfig + drivers/Makefile entries.
 3. Apply the adapted SUSFS 4.19 kernel patch committed under patches/resukisu
    (modern SUSFS API adapted to Linux 4.19; matches ReSukiSU's kernel-side
    expectations, including sus_map / avc-log spoofing / sdcard monitor).
 4. Inject the ksu_handle_* call sites ReSukiSU checks for at compile time.
    Inline-hook mode has no manual-hook auto hooks, so every call site must be
    present in the kernel source and must NOT carry CONFIG_KSU_MANUAL_HOOK
    guards (see ReSukiSU kernel/tools/inline_hook_check.mk).
 5. Record the integration lock.

The kernel tree is the synced disposable checkout under $KERNEL_DIR.
"""

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

RESUKISU_REPO = "https://github.com/ESK-Project/ReSukiSU.git"
RESUKISU_REF = os.environ.get(
    "RESUKISU_EXPECTED_REF", "e04defa03ee32c1e07ef9ad3f6be6ebefcf698ca"
)

KERNEL_DIR = pathlib.Path(os.environ.get("KERNEL_DIR", "/stock/private/msm-google"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/stock-out"))

# ReSukiSU's Kbuild refuses a plain copy of kernel/: it requires the kernel
# directory to live inside a git checkout (KSU_SRC/../.git). Clone the repo
# into the kernel tree and point drivers/kernelsu at it, matching ReSukiSU's
# official setup.sh layout.
RESUKISU_SOURCE_DIR = pathlib.Path(
    os.environ.get("RESUKISU_SOURCE_DIR", str(KERNEL_DIR / "KernelSU"))
)
SUSFS_PATCH = REPO / "patches/resukisu/50_add_susfs_in_kernel-4.19-adapted.patch"

GIT_ENV = {
    **os.environ,
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_LAZY_FETCH": "0",
}
# build-redfin-sukisu.sh exports GIT_ALLOW_PROTOCOL=file for its hermetic
# local staging; the ReSukiSU clone needs https.
GIT_ENV.pop("GIT_ALLOW_PROTOCOL", None)


def log(message):
    print(f"[resukisu-inline] {message}", flush=True)


def run(args, cwd=None, check=True, capture=True):
    result = subprocess.run(
        args,
        cwd=cwd,
        env=GIT_ENV,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Command failed: {' '.join(str(a) for a in args)}")
    return result


def git(tree, *args, check=True):
    return run(["git", "-C", str(tree), *args], check=check)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_kbuild_for_o_builds(checkout):
    """Make ReSukiSU's Kbuild work under the AOSP out-of-tree build layout.

    The clean pass includes this Kbuild through a path the kbuild framework
    resolves differently, so the Kbuild's `$(realpath ...)` for KSU_SRC comes
    back empty and the git-submodule check hard-fails during `make clean`.
    Fall back to the absolute in-tree location (known at apply time), which
    resolves the drivers/kernelsu -> KernelSU/kernel symlink, and verify the
    resulting KSU_SRC/../.git expectation right away.
    """
    kbuild = checkout / "kernel" / "Kbuild"
    text = kbuild.read_text()
    anchor = "KSU_SRC := $(realpath $(dir $(abspath $(lastword $(MAKEFILE_LIST)))))\n"
    fallback_path = KERNEL_DIR / "drivers" / "kernelsu"
    if "KSU_SRC fallback for redfin" in text:
        log("Kbuild: KSU_SRC fallback already present")
    else:
        if anchor not in text:
            raise RuntimeError("Kbuild: KSU_SRC anchor not found; ReSukiSU layout changed")
        fallback = (
            anchor
            + "# KSU_SRC fallback for redfin out-of-tree builds: depending on how this\n"
            + "# Kbuild is included (source tree vs the persistent output tree), the\n"
            + "# derived KSU_SRC may resolve outside the driver's git checkout.\n"
            + "ifeq ($(shell test -e $(KSU_SRC)/../.git && echo 1 || echo 0),0)\n"
            + f"KSU_SRC := $(realpath {fallback_path})\n"
            + "endif\n"
        )
        kbuild.write_text(text.replace(anchor, fallback))
        log("Kbuild: added KSU_SRC fallback for out-of-tree builds")

    link = KERNEL_DIR / "drivers" / "kernelsu"
    resolved = pathlib.Path(os.path.realpath(link))
    if not os.path.islink(link):
        raise RuntimeError(f"drivers/kernelsu is not a symlink after wiring: {link}")
    if not (resolved.parent / ".git").exists():
        raise RuntimeError(
            f"KSU_SRC/../.git expectation is not satisfied: {resolved.parent}/.git missing"
        )
    log(f"Kbuild check passed: KSU_SRC -> {resolved} (../.git present)")

    probe_make = shutil.which("make")
    if probe_make:
        probe = pathlib.Path("/tmp/ksu_kbuild_probe.mk")
        probe.write_text(
            "include $(PROBE_KSU_BUILD)\n"
            "$(info PROBE_MAKE_VERSION=$(MAKE_VERSION))\n"
            "$(info PROBE_KSU_SRC=$(KSU_SRC))\n"
            "$(info PROBE_LOCAL_GIT_EXISTS=$(LOCAL_GIT_EXISTS))\n"
        )
        result = run(
            [probe_make, "-f", str(probe), f"PROBE_KSU_BUILD={KERNEL_DIR}/drivers/kernelsu/Kbuild"],
            cwd=KERNEL_DIR,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        print(output)
        if "PROBE_LOCAL_GIT_EXISTS=1" not in output:
            raise RuntimeError(
                "make probe failed: the Kbuild git check does not pass in-tree"
            )
        log("make probe passed: Kbuild git check is satisfied")
    else:
        log("make not available; skipping the Kbuild make probe")


def clone_resukisu():
    """Clone ReSukiSU into the kernel tree; returns the checkout directory."""
    checkout = RESUKISU_SOURCE_DIR
    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    log(f"Cloning {RESUKISU_REPO} at {RESUKISU_REF[:12]} into {checkout}")
    run(["git", "clone", "--quiet", RESUKISU_REPO, str(checkout)])
    git(checkout, "checkout", "--quiet", RESUKISU_REF)
    head = git(checkout, "rev-parse", "HEAD").stdout.strip()
    if head != RESUKISU_REF:
        raise RuntimeError(f"ReSukiSU ref mismatch: {head}")
    return checkout


def wire_driver(checkout):
    source = checkout / "kernel"
    if not source.is_dir():
        raise RuntimeError(f"ReSukiSU kernel/ directory is missing: {source}")

    driver_link = KERNEL_DIR / "drivers" / "kernelsu"
    if driver_link.is_symlink():
        driver_link.unlink()
    elif driver_link.is_dir():
        shutil.rmtree(driver_link)
    elif driver_link.exists():
        driver_link.unlink()
    os.symlink("../KernelSU/kernel", driver_link)
    log("drivers/kernelsu -> ../KernelSU/kernel (symlink)")

    makefile = KERNEL_DIR / "drivers" / "Makefile"
    text = makefile.read_text()
    if "kernelsu" not in text:
        anchor = "obj-$(CONFIG_SENSORS_SSC)\t+= sensors/"
        if anchor in text:
            text = text.replace(anchor, anchor + "\nobj-$(CONFIG_KSU)\t\t\t+= kernelsu/")
        else:
            text = text.rstrip("\n") + "\nobj-$(CONFIG_KSU)\t\t\t+= kernelsu/\n"
        makefile.write_text(text)
        log("drivers/Makefile: added kernelsu entry")
    else:
        log("drivers/Makefile: kernelsu entry already present")

    kconfig = KERNEL_DIR / "drivers" / "Kconfig"
    text = kconfig.read_text()
    if "drivers/kernelsu/Kconfig" not in text:
        index = text.rfind("\nendmenu")
        if index < 0:
            raise RuntimeError("drivers/Kconfig: endmenu anchor not found")
        text = text[:index] + '\nsource "drivers/kernelsu/Kconfig"\n' + text[index:]
        kconfig.write_text(text)
        log("drivers/Kconfig: added kernelsu source")
    else:
        log("drivers/Kconfig: kernelsu source already present")


def apply_susfs_patch():
    if not SUSFS_PATCH.is_file():
        raise RuntimeError(f"SUSFS patch is missing: {SUSFS_PATCH}")
    log(f"Applying {SUSFS_PATCH.name} ({SUSFS_PATCH.stat().st_size} bytes)")

    check = git(KERNEL_DIR, "apply", "--check", str(SUSFS_PATCH), check=False)
    if check.returncode == 0:
        git(KERNEL_DIR, "apply", str(SUSFS_PATCH))
        log("SUSFS patch applied cleanly (git apply)")
        return

    check3 = git(KERNEL_DIR, "apply", "--check", "--3way", str(SUSFS_PATCH), check=False)
    if check3.returncode == 0:
        git(KERNEL_DIR, "apply", "--3way", str(SUSFS_PATCH))
        log("SUSFS patch applied with 3-way merge")
        return

    log("git apply failed; retrying with patch --fuzz=3")
    result = run(
        ["patch", "-p1", "--fuzz=3", "--no-backup-if-mismatch", "-i", str(SUSFS_PATCH)],
        cwd=KERNEL_DIR,
        check=False,
    )
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        for rej in sorted(KERNEL_DIR.rglob("*.rej")):
            print(f"--- reject file: {rej.relative_to(KERNEL_DIR)}", file=sys.stderr)
            print(rej.read_text(errors="replace")[:4000], file=sys.stderr)
        raise RuntimeError(
            "SUSFS patch did not apply cleanly; fix patches/resukisu/"
            "50_add_susfs_in_kernel-4.19-adapted.patch for this kernel tree"
        )
    log("SUSFS patch applied with fuzz")


class Injection:
    """One compile-time call-site injection into a kernel source file."""

    def __init__(self, relpath, marker, anchor, block, mode, expected):
        self.relpath = relpath
        self.marker = marker
        self.anchor = anchor
        self.block = block
        self.mode = mode
        self.expected = expected


def guarded(inner):
    return f"#ifdef CONFIG_KSU_SUSFS\n{inner}\n#endif\n"


INJECTIONS = [
    Injection(
        "fs/exec.c",
        "ksu_handle_execveat",
        "\treturn __do_execve_file(fd, filename, argv, envp, flags, NULL);",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,\n"
            "\t\t\t\t\t       void *argv, void *envp, int *flags);\n"
            "\t\t(void)ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);\n"
            "\t}\n"
        ),
        "before",
        1,
    ),
    Injection(
        "fs/open.c",
        "ksu_handle_faccessat",
        "\tint res;\n\tunsigned int lookup_flags = LOOKUP_FOLLOW;\n",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_faccessat(int *dfd, const char __user **filename_user,\n"
            "\t\t\t\t\t\tint *mode, int *__unused_flags);\n"
            "\t\tksu_handle_faccessat(&dfd, &filename, &mode, NULL);\n"
            "\t}\n"
        ),
        "after",
        1,
    ),
    Injection(
        "fs/read_write.c",
        "ksu_handle_sys_read",
        "\treturn ksys_read(fd, buf, count);",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_sys_read(unsigned int fd, char __user **buf_ptr,\n"
            "\t\t\t\t\t       size_t *count_ptr);\n"
            "\t\tksu_handle_sys_read(fd, &buf, &count);\n"
            "\t}\n"
        ),
        "before",
        None,
    ),
    Injection(
        "fs/stat.c",
        "ksu_handle_stat",
        "\terror = vfs_fstatat(dfd, filename, &stat, flag);",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_stat(int *dfd, const char __user **filename_user,\n"
            "\t\t\t\t\t   int *flags);\n"
            "\t\tksu_handle_stat(&dfd, &filename, &flag);\n"
            "\t}\n"
        ),
        "before",
        None,
    ),
    Injection(
        "kernel/sys.c",
        "ksu_handle_setresuid",
        "\tkuid_t kruid, keuid, ksuid;\n",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_setresuid(uid_t ruid, uid_t euid, uid_t suid);\n"
            "\t\t(void)ksu_handle_setresuid(ruid, euid, suid);\n"
            "\t}\n"
        ),
        "after",
        1,
    ),
    Injection(
        "kernel/reboot.c",
        "ksu_handle_sys_reboot",
        "\t/* We only trust the superuser with rebooting the system. */",
        "#ifdef CONFIG_KSU\n"
        "\t{\n"
        "\t\textern int ksu_handle_sys_reboot(int magic1, int magic2,\n"
        "\t\t\t\t\t\t unsigned int cmd, void __user **arg);\n"
        "\t\tint ksu_susfs_reboot_ret = ksu_handle_sys_reboot(magic1, magic2, cmd, &arg);\n"
        "\t\tif (ksu_susfs_reboot_ret)\n"
        "\t\t\tgoto ksu_susfs_original_flow;\n"
        "\t\treturn ksu_susfs_reboot_ret;\n"
        "\t}\n"
        "ksu_susfs_original_flow:\n"
        "#endif\n",
        "before",
        1,
    ),
    Injection(
        "drivers/input/input.c",
        "ksu_handle_input_handle_event",
        "\tint disposition = input_get_disposition(dev, type, code, &value);",
        guarded(
            "\t{\n"
            "\t\textern int ksu_handle_input_handle_event(unsigned int *type,\n"
            "\t\t\t\t\t\t\t unsigned int *code, int *value);\n"
            "\t\tksu_handle_input_handle_event(&type, &code, &value);\n"
            "\t}\n"
        ),
        "before",
        1,
    ),
]


def inject_call_sites():
    for spec in INJECTIONS:
        path = KERNEL_DIR / spec.relpath
        text = path.read_text()
        if spec.marker in text:
            log(f"{spec.relpath}: {spec.marker} already present, skipping")
            continue
        count = text.count(spec.anchor)
        if count == 0:
            raise RuntimeError(
                f"{spec.relpath}: anchor for {spec.marker} not found; "
                "kernel tree drifted"
            )
        if spec.expected is not None and count != spec.expected:
            raise RuntimeError(
                f"{spec.relpath}: expected {spec.expected} anchor(s) for "
                f"{spec.marker}, found {count}"
            )
        if spec.mode == "before":
            text = text.replace(spec.anchor, spec.block + spec.anchor)
        else:
            text = text.replace(spec.anchor, spec.anchor + spec.block)
        path.write_text(text)
        log(f"{spec.relpath}: injected {spec.marker} ({count} site(s))")


def verify_patch_result():
    """Sanity-check that every patched file really carries the patch content.

    For each target file, pick the identifiers the patch adds (susfs_*/ksu_*
    symbols and CONFIG_KSU_SUSFS options) and require at least one of them to
    be present after the apply. This catches hunks that were fuzzed into the
    wrong location.
    """
    text = SUSFS_PATCH.read_text(errors="replace")
    new_files = {"fs/susfs.c", "include/linux/susfs.h", "include/linux/susfs_def.h"}
    checked = 0
    for section in re.split(r"(?m)^(?=diff --git )", text):
        match = re.match(r"diff --git a/(\S+)", section)
        if not match:
            continue
        relpath = match.group(1)
        path = KERNEL_DIR / relpath
        if not path.is_file():
            raise RuntimeError(f"SUSFS patch target is missing after apply: {relpath}")
        if relpath in new_files:
            checked += 1
            continue
        added = [
            line[1:]
            for line in section.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        markers = set()
        for line in added:
            markers.update(re.findall(r"\b(?:susfs|ksu)_[A-Za-z0-9_]+", line))
            markers.update(re.findall(r"CONFIG_KSU_SUSFS[A-Z_]*", line))
        if not markers:
            log(f"{relpath}: no distinctive marker in patch, skipping content check")
            checked += 1
            continue
        content = path.read_text(errors="replace")
        if not any(marker in content for marker in markers):
            raise RuntimeError(
                f"SUSFS patch target {relpath} carries none of the expected "
                f"markers after apply: {sorted(markers)[:6]}"
            )
        checked += 1
    log(f"SUSFS patch verification passed ({checked} files)")


def write_lock(resukisu_head):
    lock_path = OUT_DIR / "sukisu-susfs.lock"
    fields = [
        ("INTEGRATION_MODE", "resukisu-susfs-inline"),
        ("KSU_DRIVER", "ReSukiSU"),
        ("KSU_REPO", RESUKISU_REPO),
        ("KSU_REF", resukisu_head),
        ("KSU_CHECKOUT", str(RESUKISU_SOURCE_DIR)),
        ("KSU_HOOK_MODE", "CONFIG_KSU_SUSFS (SUSFS Inline Hook)"),
        ("SUSFS_PATCH", str(SUSFS_PATCH.relative_to(REPO))),
        ("SUSFS_PATCH_SHA256", sha256_file(SUSFS_PATCH)),
        ("SUSFS_KERNEL_REF", "simonpunk susfs4ksu kernel-4.19 adapted (Kevin233B/TALIH-PD2)"),
        ("KERNEL_DIR", str(KERNEL_DIR)),
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("".join(f"{key}={value}\n" for key, value in fields))
    log(f"Wrote {lock_path}")
    print(lock_path.read_text())


def main():
    if not KERNEL_DIR.is_dir():
        print(f"Error: kernel tree is missing: {KERNEL_DIR}", file=sys.stderr)
        return 1
    try:
        checkout = clone_resukisu()
        wire_driver(checkout)
        patch_kbuild_for_o_builds(checkout)
        apply_susfs_patch()
        verify_patch_result()
        inject_call_sites()
        write_lock(RESUKISU_REF)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
