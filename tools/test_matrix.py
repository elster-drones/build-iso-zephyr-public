"""Exercise each canonical upstream simulator target once, retaining every result."""

from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import build
import runtime


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zephyr-base", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--target", action="append")
    p.add_argument("--reuse-builds", action="store_true")
    args = p.parse_args()
    base = args.zephyr_base.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "ZEPHYR_BASE": str(base),
        "CMAKE_BUILD_PARALLEL_LEVEL": "2",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(base.parent / "*"),
    }
    targets = {}
    for target in build.TARGETS.values():
        if target.get("simulators") and (
            not args.target or target["canonical_target"] in args.target
        ):
            targets[target["canonical_target"]] = target
    for name in runtime.PROFILES:
        if not args.target or name in args.target:
            targets[name] = build.TARGETS[name]

    def exercise(name, target):
        directory = args.output / name.replace("/", "_").replace("@", "_")
        directory.mkdir(exist_ok=True)
        start = time.monotonic()
        result = {
            "target": name,
            "status": "blocked",
            "simulators": target.get("simulators", []),
        }
        if not target["buildable"]:
            result["message"] = target["unavailable_reason"]
            return result
        sims = target.get("simulators") or [{"name": "renode", "exec": "renode"}]
        available = any(
            s["name"] in ("native", "qemu") or shutil.which(s.get("exec") or s["name"])
            for s in sims
        )
        if not available:
            result["message"] = (
                "Required simulator tool or vendor license is unavailable"
            )
            return result
        if target["architecture"] == "posix":
            if "_bsim" in target["board"] or "bsim" in target["board"]:
                components = Path(
                    env.get(
                        "BSIM_COMPONENTS_PATH",
                        str(base.parent / "tools/bsim/components"),
                    )
                )
                if not components.exists():
                    result["message"] = "BabbleSim runtime components are unavailable"
                    return result
            if not name.endswith("/64"):
                import tempfile

                with tempfile.TemporaryDirectory() as temporary:
                    probe = subprocess.run(
                        [
                            "gcc",
                            "-m32",
                            "-x",
                            "c",
                            "-o",
                            str(Path(temporary) / "probe"),
                            "-",
                        ],
                        input="#include <stdlib.h>\nint main(void) {return 0;}\n",
                        text=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                if probe.returncode:
                    result["message"] = (
                        "The native 32-bit host C library and development headers are unavailable"
                    )
                    return result
        try:
            build_dir = directory / "build"
            local_env = dict(env)
            if target["architecture"] == "posix":
                local_env["ZEPHYR_TOOLCHAIN_VARIANT"] = "host"
            with (directory / "build.log").open("wb") as log:
                proc = (
                    subprocess.CompletedProcess([], 0)
                    if args.reuse_builds and (build_dir / "zephyr/zephyr.elf").is_file()
                    else subprocess.run(
                        [
                            "west",
                            "build",
                            "-p",
                            "never" if args.reuse_builds else "always",
                            "-b",
                            name,
                            "-d",
                            str(build_dir),
                            str(base / "samples/hello_world"),
                        ],
                        cwd=base,
                        env=local_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=360,
                    )
                )
            if proc.returncode:
                result.update(status="build_failed", message="See retained build.log")
                return result
            if target["architecture"] == "x86_64":
                subprocess.run(
                    [
                        "cmake",
                        "--build",
                        str(build_dir),
                        "--target",
                        "qemu_kernel_target",
                    ],
                    env=local_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            config = {"board": name, "application": "samples/hello_world"}
            build.package(
                build_dir, config, base.parent, local_env, directory / "output"
            )
            bundle = directory / "output/zephyr-firmware.zip"
            runtime.init_twister(base)
            contract = runtime.describe(bundle, base)
            (directory / "contract.json").write_text(json.dumps(contract, indent=2))
            result.update(runtime.run(bundle, contract, base, directory / "evidence"))
        except Exception as exc:
            result.update(status="error", message=str(exc)[:2000])
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        return result

    report_path = args.output / "matrix.json"
    previous = (
        json.loads(report_path.read_text())
        if args.target and report_path.exists()
        else []
    )
    results = [row for row in previous if row["target"] not in targets]
    with ThreadPoolExecutor(max_workers=max(1, min(4, args.jobs))) as pool:
        futures = {
            pool.submit(exercise, name, target): name
            for name, target in targets.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            (args.output / "matrix.json").write_text(
                json.dumps(sorted(results, key=lambda r: r["target"]), indent=2)
            )
            print(
                result["target"],
                result["status"],
                result.get("message", ""),
                flush=True,
            )


if __name__ == "__main__":
    main()
