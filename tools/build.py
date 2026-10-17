"""Thin, pinned West adapter. Firmware layouts remain owned by Zephyr."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "dccb09599635bdff17633fa7e9dab014b91dce90"
VERSION = "4.4.2"
SDK_VERSION = "1.0.1"
BOARDS = {
    "qemu_x86_64": ("x86_64", "x86_64-zephyr-elf"),
    "nrf52840dk/nrf52840": ("arm", "arm-zephyr-eabi"),
    "esp32_devkitc/esp32/procpu": ("xtensa", "xtensa-espressif_esp32_zephyr-elf"),
}


def run(*command, cwd=None, env=None):
    subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True)


def inside(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes application workspace: {value}")
    return path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bootstrap(cache: Path):
    """Serialize shared source/SDK preparation; builds only read this cache."""
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        venv = cache / "venv"
        python = venv / "bin/python"
        if not python.exists():
            run(sys.executable, "-m", "venv", venv)
        env = dict(os.environ, PATH=f"{venv}/bin:{os.environ['PATH']}")
        workspace = cache / "workspace"
        ready = cache / "ready"
        dependency_digest = sha256(ROOT / "requirements-lock.txt")
        dependency_marker = cache / "python-ready"
        if not dependency_marker.exists() or dependency_marker.read_text().strip() != dependency_digest:
            run(python, "-m", "pip", "install", "-r", ROOT / "requirements-lock.txt")
            dependency_marker.write_text(dependency_digest + "\n")
        if not ready.exists():
            run(python, "-m", "pip", "install", "west==1.4.0", "cmake==3.31.6", "ninja==1.11.1.4")
            if not (workspace / ".west").exists():
                manifest = workspace / "manifest"
                manifest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / "west.yml", manifest / "west.yml")
                run("west", "init", "-l", manifest, env=env)
            run("west", "update", "--narrow", "-o=--depth=1", cwd=workspace, env=env)
            run(python, "-m", "pip", "install", "-r", ROOT / "requirements-lock.txt")
            ready.write_text(REVISION + "\n")
        if ready.read_text().strip() != REVISION:
            raise ValueError("Source cache revision mismatch")
        sdk = cache / f"zephyr-sdk-{SDK_VERSION}"
        run("west", "sdk", "install", "--version", SDK_VERSION, "--install-dir", sdk,
            "--gnu-toolchains", *(tc for _, tc in BOARDS.values()), cwd=workspace / "zephyr", env=env)
    env.update(ZEPHYR_SDK_INSTALL_DIR=str(sdk), ZEPHYR_TOOLCHAIN_VARIANT="zephyr",
               ZEPHYR_BASE=str(workspace / "zephyr"), CMAKE_BUILD_PARALLEL_LEVEL=os.environ.get("JOBS", "4"))
    return workspace, env


def package(build: Path, config: dict, workspace: Path, env: dict, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    staging = build / "bundle"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    # Preserve runner/domain-relative layout, including ESP32 bootloader images.
    suffixes = {".elf", ".bin", ".hex", ".uf2", ".yaml", ".yml", ".json", ".dts", ".map"}
    for path in build.rglob("*"):
        if staging in path.parents or not path.is_file() or path.is_symlink():
            continue
        if path.suffix in suffixes or path.name == ".config":
            target = staging / path.relative_to(build)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    frozen = subprocess.check_output(["west", "manifest", "--freeze", "--active-only"], cwd=workspace, env=env)
    (staging / "west-frozen.yml").write_bytes(frozen)
    (staging / "build-config.json").write_text(json.dumps(config, indent=2) + "\n")
    (staging / "python-packages.txt").write_bytes(subprocess.check_output(
        [str(Path(env["PATH"].split(os.pathsep)[0]) / "python"), "-m", "pip", "freeze"], env=env))
    for name in config.get("kconfig_fragments", []) + config.get("devicetree_overlays", []):
        target = inside(staging, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(ROOT, name), target)
    custom = inside(ROOT, config["application"]) if not config["application"].startswith("samples/") else None
    if custom is not None and custom.is_dir():
        if any(p.is_symlink() for p in custom.rglob("*")):
            raise ValueError("Custom application source cannot contain symlinks")
        shutil.copytree(custom, inside(staging, config["application"]), symlinks=False)
    flash_instructions = ""
    if config["board"].startswith("esp32"):
        python = Path(env["PATH"].split(os.pathsep)[0]) / "python"
        commands = json.loads(subprocess.check_output([str(python), str(ROOT / "tools/flash_commands.py"), str(build)], env=env))
        (staging / "flash-commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        flash_instructions = "Run these upstream-generated commands after extracting this ZIP (esptool 5.0.2):\n" + "\n".join(shlex.join(cmd) for cmd in commands) + "\n"
    elif config["board"] == "nrf52840dk/nrf52840":
        flash_instructions = "With a compatible debug probe connected, flash the address-bearing HEX file:\npyocd flash --target nrf52840 zephyr/zephyr.hex\n"
    (staging / "README.txt").write_text(
        f"Zephyr {VERSION}, board {config['board']}, SDK {SDK_VERSION}\n"
        "Reproduce with this repository, build-config.json, the configuration/source files, and west-frozen.yml.\n"
        "Firmware is compiled; physical hardware operation requires a separate board test.\n"
        + flash_instructions + "The browser QEMU shell is an emulator, not a hardware test.\n")
    architecture = BOARDS[config["board"]][0]
    files = {str(p.relative_to(staging)): sha256(p) for p in sorted(staging.rglob("*")) if p.is_file()}
    qemu = config["board"] == "qemu_x86_64"
    runtime = {"kernel": "zephyr/zephyr-qemu-locore.elf", "loader": "zephyr/zephyr-qemu-main.elf"} if qemu else None
    if runtime and not all(name in files for name in runtime.values()):
        raise ValueError("Missing QEMU companion images")
    manifest = {"schema_version": 1, "zephyr_version": VERSION, "zephyr_revision": REVISION,
                "sdk_version": SDK_VERSION, "board": config["board"], "architecture": architecture,
                "runtime": runtime, "files": files}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    bundle = output / "zephyr-firmware.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(str(path.relative_to(staging)), (2026, 8, 7, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    (output / "result.json").write_text(json.dumps({"artifact": bundle.name, "sha256": sha256(bundle)}) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/zephyr.json")
    parser.add_argument("--cache", default=os.environ.get("ZEPHYR_CACHE_DIR", str(Path.home() / ".cache/openfactory/zephyr")))
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    config = json.loads(inside(ROOT, args.config).read_text())
    if config.get("board") not in BOARDS:
        raise ValueError("Unsupported board")
    cache = Path(args.cache).resolve() / VERSION
    workspace, env = bootstrap(cache)
    application = config.get("application", "samples/hello_world")
    source = inside(workspace / "zephyr", application) if application.startswith("samples/") else inside(ROOT, application)
    if not (source / "CMakeLists.txt").is_file():
        raise ValueError("Application requires CMakeLists.txt")
    build = ROOT / "build"
    command = ["west", "build", "-p", "always", "-b", config["board"], "-d", str(build)]
    if config["board"].startswith("esp32"):
        command.append("--sysbuild")
    command.extend([str(source), "--"])
    for key, cmake in (("kconfig_fragments", "EXTRA_CONF_FILE"), ("devicetree_overlays", "DTC_OVERLAY_FILE")):
        paths = [str(inside(ROOT, name)) for name in config.get(key, [])]
        if paths:
            command.append(f"-D{cmake}={';'.join(paths)}")
    run(*command, cwd=workspace, env=env)
    if config["board"] == "qemu_x86_64":
        run("cmake", "--build", build, "--target", "qemu_kernel_target", env=env)
    package(build, config, workspace, env, inside(ROOT, args.output))


if __name__ == "__main__":
    main()
