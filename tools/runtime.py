"""Run the delivered firmware, using pinned Zephyr simulator data and harnesses.

The builder records upstream-generated launch commands, never a second table of
QEMU CPU flags. No compilation is performed by this runner. Console assertions
are evaluated by Twister; interactive application tests belong to the app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tempfile
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "dccb09599635bdff17633fa7e9dab014b91dce90"
PROFILES = json.loads((ROOT / "runtime/profiles.json").read_text())


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def payload_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inside(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Runtime input escapes its workspace")
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Runtime input escapes its workspace")
    return path


def init_twister(base):
    if (
        subprocess.check_output(
            ["git", "-c", f"safe.directory={base}", "rev-parse", "HEAD"],
            cwd=base,
            text=True,
        ).strip()
        != REVISION
    ):
        raise ValueError("Simulator discovery requires the pinned Zephyr checkout")
    os.environ["ZEPHYR_BASE"] = str(base)
    sys.path[:0] = [
        str(base / p)
        for p in ("scripts", "scripts/pylib/twister", "scripts/pylib/python-devicetree")
    ]


def application_test(staging, config, base):
    applications = json.loads((ROOT / "runtime/application-tests.json").read_text())
    if config["application"] in applications:
        definition = applications[config["application"]]
        return {
            **definition,
            "harness": "application",
            "script_sha256": digest(ROOT / definition["script"]),
        }
    import yaml

    source = (
        base / config["application"]
        if config["application"].startswith("samples/")
        else inside(staging, config["application"])
    )
    metadata = next(
        (
            source / n
            for n in ("testcase.yaml", "sample.yaml")
            if (source / n).is_file()
        ),
        None,
    )
    if metadata is None:
        return None
    data = yaml.safe_load(metadata.read_text())
    # A variant requiring another build cannot test the bytes we already have.
    candidates = [
        (name, {**data.get("common", {}), **suite})
        for name, suite in data.get("tests", {}).items()
    ]
    candidates = [
        (name, suite)
        for name, suite in candidates
        if not any(
            suite.get(k)
            for k in (
                "extra_args",
                "conf_files",
                "extra_conf_files",
                "extra_dtc_overlay_files",
                "build_only",
                "skip",
            )
        )
    ]
    if len(candidates) != 1:
        return None
    name, suite = candidates[0]
    return {
        "id": name,
        "harness": suite.get("harness", "test"),
        "harness_config": suite.get("harness_config", {}),
        "timeout": min(300, max(1, int(suite.get("timeout", 60)))),
        "metadata_sha256": digest(metadata),
        "source": config["application"] + "/" + metadata.name,
    }


def capture_runtime(build, staging, config, base, env):
    """Capture only the generated run action; do not retain a build target."""
    registry = json.loads((ROOT / "targets.v1.json").read_text())["targets"]
    launches = []
    for simulator in registry[config["board"]].get("simulators", []):
        name = simulator["name"]
        if name == "renode" and simulator.get("script") and simulator.get("uart"):
            launches.append(dict(simulator))
            continue
        if name == "native":
            simulator = {**simulator, "exec": "true"}
            command = shlex.quote(str(build / "zephyr/zephyr.exe"))
        else:
            proc = subprocess.run(
                ["ninja", "-C", str(build), "-t", "commands", "run_" + name],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if proc.returncode or not proc.stdout.strip():
                continue
            command = proc.stdout.strip().splitlines()[-1]
        command = (
            command.replace(str(build), "${FIRMWARE}")
            .replace(str(base), "${ZEPHYR_BASE}")
            .replace(str(build.parent), "${FIRMWARE}")
        )
        required = simulator.get("exec")
        if name == "qemu":
            required = next(
                (
                    token
                    for token in shlex.split(command)
                    if re.search(r"(^|/)qemu-system-[a-zA-Z0-9_-]+$", token)
                ),
                None,
            )
        if required and "/" in required:
            command = command.replace(required, Path(required).name)
            required = Path(required).name
        launches.append({**simulator, "exec": required, "command": command})
    descriptor = {
        "schema_version": 1,
        "board": config["board"],
        "zephyr_revision": REVISION,
        "launches": launches,
        "test": application_test(staging, config, base),
    }
    target = staging / "runtime/launch.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(descriptor, indent=2) + "\n")


def read_bundle(bundle, output=None):
    with zipfile.ZipFile(bundle) as z:
        entries = z.infolist()
        if (
            len(entries) > 10000
            or sum(e.file_size for e in entries) > 512 * 1024 * 1024
        ):
            raise ValueError("Oversized firmware bundle")
        names = [e.filename for e in entries]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate archive members")
        for e in entries:
            inside(Path("/firmware"), e.filename)
            if (e.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Firmware symlinks are not executable inputs")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("zephyr_revision") != REVISION:
            raise ValueError("Firmware is not from the pinned Zephyr release")
        files = manifest.get("files") or {}
        if not files:
            raise ValueError("Missing firmware integrity manifest")
        for name, expected in files.items():
            data = z.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Firmware integrity mismatch")
            if output:
                path = inside(output, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        if "build-config.json" not in files:
            raise ValueError(
                "Build configuration is not covered by the integrity manifest"
            )
        config = json.loads(z.read("build-config.json"))
        if config.get("board") != manifest.get("board"):
            raise ValueError("Firmware board metadata mismatch")
        runtime = (
            json.loads(z.read("runtime/launch.json"))
            if "runtime/launch.json" in files
            else {}
        )
    return manifest, config, runtime


def describe(bundle, base):
    manifest, config, runtime = read_bundle(bundle)
    target = config["board"]
    if runtime and (
        runtime.get("board") != target or runtime.get("zephyr_revision") != REVISION
    ):
        raise ValueError("Runtime metadata belongs to another target")
    test = runtime.get("test")
    if test is None:
        # Existing immutable ZIPs can use the exact upstream sample metadata.
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            read_bundle(bundle, Path(d))
            test = application_test(Path(d), config, base)
    profile = PROFILES.get(target)
    launches = runtime.get("launches") or []
    if profile and (profile["name"] == "renode" or not launches):
        launches = [
            {
                "name": profile["name"],
                "exec": profile.get("exec") or "renode",
                "profile": target,
            }
        ]
    registry = json.loads((ROOT / "targets.v1.json").read_text())["targets"]
    simulators = registry.get(target, {}).get("simulators") or []
    if not launches:
        launches = [
            dict(s)
            for s in simulators
            if s["name"] == "renode" and s.get("script") and s.get("uart")
        ]
    allowed = {sim["name"]: sim for sim in simulators}
    if profile:
        allowed[profile["name"]] = profile
    for launch in launches:
        name = launch["name"]
        if name not in allowed:
            raise ValueError(
                "Firmware declares a simulator outside its pinned board metadata"
            )
        executable = Path(launch.get("exec") or name).name
        if name == "qemu" and not re.fullmatch(
            r"qemu-system-[A-Za-z0-9_-]+", executable
        ):
            raise ValueError("Invalid QEMU executable")
        if (
            name not in ("native", "qemu")
            and executable != Path(allowed[name].get("exec") or name).name
        ):
            raise ValueError(
                "Simulator executable differs from the pinned board metadata"
            )
        launch["exec"] = executable
    contract = {
        "schema_version": 1,
        "artifact_sha256": digest(bundle),
        "board": target,
        "zephyr_revision": REVISION,
        "application": config["application"],
        "test": test,
        "executables": {
            n: h for n, h in manifest["files"].items() if n.endswith((".elf", ".exe"))
        },
        "launches": launches,
        "simulators": simulators,
        "runtime_profile_sha256": payload_hash(
            {
                str(p.relative_to(ROOT)): digest(p)
                for p in sorted((ROOT / "runtime").rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts
            }
        ),
        "runner_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
    }
    # An unsupported case is still a recorded test attempt, never an empty pass.
    contract["blocked_reason"] = (
        "No runtime test definition is available for this exact application"
        if not test
        else "This firmware needs a runnable console or application test harness"
        if test["harness"] not in ("console", "test", "ztest", "application")
        else "No emulator profile is available for the exact delivered target"
        if not launches and not simulators
        else "The retained firmware lacks its upstream simulator launch inputs; rebuild this variant"
        if not launches
        else None
    )
    contract["contract_hash"] = payload_hash(contract)
    return contract


def renode_command(profile, firmware, output, base):
    executable = shutil.which("renode")
    if not executable:
        raise FileNotFoundError("Renode is not installed on the test worker")
    root = Path(executable).resolve().parent
    uart = output / "uart.log"
    script = output / "boot.resc"
    version = subprocess.check_output(
        [executable, "--version"], text=True, timeout=10
    ).strip()
    if profile.get("script"):
        upstream = inside(base, profile["script"])
        script.write_text(
            f'$elf=@{firmware}/zephyr/zephyr.elf\ninclude @{upstream}\n{profile["uart"]} CreateFileBackend @{uart}\nemulation RunFor "2"\nquit\n'
        )
        return (
            [executable, "--plain", "--console", "--disable-xwt", str(script)],
            uart,
            version,
        )
    source = root / profile["model"]
    # The upstream STM32L151 model's fixed PLLRDY tag cannot model PLL disable.
    # Use a board-owned ready/status model; never patch the delivered ELF.
    model = (
        source.read_text()
        .replace("size: 0xC000", "size: 0x8000")
        .replace("size:0x60000", "size:0x20000")
    )
    model = model.replace('        Tag <0x40023800 4> "RCC_CR" 0x2020202', "")
    model = model.replace(
        "https://dl.antmicro.com/projects/renode/svd/STM32L1xx.svd.gz",
        str(ROOT / "runtime/wistrio/STM32L1xx.svd.gz"),
    )
    for name, address, filename in [
        ("rccCr", "0x40023800", "rcc_cr.py"),
        ("rccCfgr", "0x40023808", "rcc_cfgr.py"),
    ]:
        model += f'\n{name}: Python.PythonPeripheral @ sysbus {address}\n    size: 0x4\n    initable: true\n    filename: "{ROOT}/runtime/wistrio/{filename}"\n'
    repl = output / "platform.repl"
    repl.write_text(model)
    uart = output / "uart.log"
    script = output / "boot.resc"
    script.write_text(
        f'mach create "firmware"\nmachine LoadPlatformDescription @{repl}\nsysbus LoadELF @{firmware}/zephyr/zephyr.elf\n{profile["uart"]} CreateFileBackend @{uart}\nemulation RunFor "2"\nquit\n'
    )
    version = subprocess.check_output(
        [executable, "--version"], text=True, timeout=10
    ).strip()
    return (
        [executable, "--plain", "--console", "--disable-xwt", str(script)],
        uart,
        version,
    )


def console_harness(test, base, output):
    from twisterlib.config_parser import TwisterConfigParser
    from twisterlib.testsuite import TestSuite
    from twisterlib.testinstance import TestInstance
    from twisterlib.platform import Platform
    from twisterlib.harness import HarnessImporter

    data = {
        key: value.get(
            "default",
            []
            if value["type"] in ("list", "set")
            else {}
            if value["type"] == "map"
            else "",
        )
        for key, value in TwisterConfigParser.testsuite_valid_keys.items()
    }
    data.update(
        harness=test["harness"],
        harness_config=test["harness_config"],
        timeout=test["timeout"],
    )
    suite = TestSuite(str(base), str(base / "samples"), test["id"], data)
    instance = TestInstance(suite, Platform(), "zephyr", str(output))
    instance.handler = SimpleNamespace(options=SimpleNamespace(verbose=0))
    harness = HarnessImporter.get_harness(test["harness"].capitalize())
    harness.configure(instance)
    return harness, instance


def isolate(command, firmware, engine, base, executable):
    """Keep untrusted firmware away from the trusted runner's reports and inputs."""
    if not shutil.which("bwrap"):
        raise FileNotFoundError("The firmware process sandbox is unavailable")
    wrapper = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/etc",
    ]
    for name in ("/usr", "/lib", "/lib64", "/bin", "/sbin"):
        if Path(name).exists():
            wrapper += ["--ro-bind", name, name]
    for name in ("ld.so.cache", "localtime", "nsswitch.conf"):
        path = "/etc/" + name
        if Path(path).exists():
            wrapper += ["--ro-bind", path, path]
    roots = [ROOT, base.parent, Path(sys.executable).parent.parent]
    if executable:
        roots.append(Path(shutil.which(executable) or executable).resolve().parent)
    if os.environ.get("ZEPHYR_SDK_INSTALL_DIR"):
        roots.append(Path(os.environ["ZEPHYR_SDK_INSTALL_DIR"]))
    for root in dict.fromkeys(roots):
        wrapper += ["--ro-bind", str(root), str(root)]
    for directory in (firmware, engine):
        wrapper += ["--bind", str(directory), str(directory)]
    wrapper += [
        "--chdir",
        str(firmware),
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PATH",
        os.environ.get("PATH", "/usr/bin:/bin"),
    ]
    return wrapper + command


def run(bundle, contract, base, output):
    expected = describe(bundle, base)
    if expected != contract:
        raise ValueError("Artifact or runtime test contract changed since admission")
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "error",
        "failure_kind": "contract",
        "artifact_sha256": contract["artifact_sha256"],
        "board": contract["board"],
        "application": contract["application"],
        "contract_hash": contract["contract_hash"],
        "zephyr_revision": REVISION,
        "hardware_tested": False,
        "scope": "emulator",
        "assertions": [],
    }
    if contract["blocked_reason"]:
        report["message"] = contract["blocked_reason"]
        return report
    workspace = Path(tempfile.mkdtemp(prefix="execution-", dir=output))
    firmware = workspace / "firmware"
    read_bundle(bundle, firmware)
    launch = next(
        (s for s in contract["launches"] if shutil.which(s.get("exec") or s["name"])),
        None,
    )
    if not launch:
        report.update(
            failure_kind="infrastructure",
            message="Required simulator executable is unavailable on the test worker",
        )
        return report
    if launch["name"] == "native":
        inside(firmware, "zephyr/zephyr.exe").chmod(0o500)
    engine = workspace / "engine"
    engine.mkdir(exist_ok=True)
    log_source = None
    try:
        if launch["name"] == "renode":
            command, log_source, version = renode_command(
                PROFILES[launch["profile"]] if launch.get("profile") else launch,
                firmware,
                engine,
                base,
            )
        else:
            command = (
                [
                    part.replace("${FIRMWARE}", str(firmware))
                    for part in PROFILES[launch["profile"]]["argv"]
                ]
                if launch.get("profile")
                else [
                    "/bin/sh",
                    "-c",
                    launch["command"]
                    .replace("${FIRMWARE}", str(firmware))
                    .replace("${ZEPHYR_BASE}", str(base)),
                ]
            )
            executable = launch.get("exec") or launch["name"]
            version = (
                subprocess.check_output(
                    [executable, "--version"],
                    text=True,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                )[:2048]
                if launch["name"] != "native"
                else "native host simulator"
            )
        command = isolate(command, firmware, engine, base, launch.get("exec"))
        report.update(
            simulator=launch["name"],
            simulator_version=version,
            profile_sha256=contract["runtime_profile_sha256"],
        )
        if contract["test"]["harness"] == "application":
            test = contract["test"]
            with (output / "simulator.log").open("wb") as log:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / test["script"]),
                        str(firmware / "zephyr"),
                        "--suite",
                        test["suite"],
                        "--command-json",
                        json.dumps(command),
                    ],
                    stdout=log,
                    stderr=log,
                    timeout=test["timeout"],
                )
            console = firmware / "serial-evidence.log"
            if console.exists():
                shutil.copy2(console, output / "console.log")
            passed = (
                proc.returncode == 0 and console.exists() and console.stat().st_size > 0
            )
            if any(
                digest(inside(firmware, name)) != expected
                for name, expected in contract["executables"].items()
            ):
                raise ValueError(
                    "Delivered executable changed during application testing"
                )
            report.update(
                status="passed" if passed else "failed",
                failure_kind=None if passed else "assertion",
                message="Firmware application runtime assertions passed"
                if passed
                else "Firmware application runtime assertions failed",
                assertions=[
                    {"id": test["id"], "status": "passed" if passed else "failed"}
                ],
                console_sha256=digest(console) if console.exists() else None,
            )
            if digest(bundle) != contract["artifact_sha256"]:
                raise ValueError("Firmware changed during application testing")
            return report
        harness, instance = console_harness(contract["test"], base, output)
        env = {
            k: os.environ[k]
            for k in ("PATH", "ZEPHYR_BASE", "ZEPHYR_SDK_INSTALL_DIR")
            if k in os.environ
        }
        env.update(HOME=str(output / "home"), TMPDIR="/tmp")
        with (output / "simulator.log").open("wb") as log:
            proc = subprocess.Popen(
                command,
                cwd=firmware,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log if log_source else subprocess.PIPE,
                stderr=log,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + contract["test"]["timeout"]
                text = ""
                partial = b""
                offset = 0
                selector = selectors.DefaultSelector()
                if not log_source:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                with (output / "console.log").open("wb") as console:
                    while time.monotonic() < deadline:
                        chunk = b""
                        if log_source:
                            if log_source.exists():
                                with os.fdopen(
                                    os.open(log_source, os.O_RDONLY | os.O_NOFOLLOW),
                                    "rb",
                                ) as f:
                                    f.seek(offset)
                                    chunk = f.read(65536)
                                    offset += len(chunk)
                            time.sleep(0.02)
                        elif selector.select(0.1):
                            chunk = os.read(proc.stdout.fileno(), 65536)
                        if chunk:
                            console.write(chunk)
                            console.flush()
                            partial += chunk
                            if console.tell() > 4 * 1024 * 1024:
                                raise ValueError(
                                    "Firmware console output limit exceeded"
                                )
                            while b"\n" in partial:
                                line, partial = partial.split(b"\n", 1)
                                harness.handle(
                                    line.decode(errors="replace").rstrip("\r")
                                )
                        if harness.status.value in ("failed", "error"):
                            break
                        # Keep a short observation tail so an immediate fatal error after a greeting fails.
                        if harness.status.value == "passed" and not text:
                            text = "passed"
                            deadline = min(deadline, time.monotonic() + 0.5)
                        if proc.poll() is not None and not chunk:
                            break
                from twisterlib.statuses import TwisterStatus

                passed = harness.status == TwisterStatus.PASS and not harness.fault
                report.update(
                    status="passed" if passed else "failed",
                    failure_kind=None if passed else "assertion",
                    message="Firmware runtime assertions passed"
                    if passed
                    else "Firmware did not satisfy its runtime assertions",
                    assertions=[
                        {"id": c.name, "status": c.status.value, "reason": c.reason}
                        for c in instance.testcases
                    ],
                    console_sha256=digest(output / "console.log"),
                )
                if not report["assertions"]:
                    report.update(
                        status="error",
                        failure_kind="contract",
                        message="No runtime assertions executed",
                    )
            finally:
                selector.close() if "selector" in locals() else None
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        (output / "runner-errors.log").write_text(str(e))
        report.update(
            status="error",
            failure_kind="infrastructure",
            message="The simulator could not execute this firmware; operator diagnostics are retained.",
        )
    if any(
        digest(inside(firmware, name)) != expected
        for name, expected in contract["executables"].items()
    ):
        raise ValueError("Delivered firmware executable changed during testing")
    if digest(bundle) != contract["artifact_sha256"]:
        raise ValueError("Firmware artifact changed during testing")
    return report


def enrich(bundle, build, base):
    """Attach launch inputs to a newly compiled, not-yet-published ZIP.

    Existing authored worktrees may contain an older builder. Its compiler
    output still gets the current trusted runtime bridge before platform signing.
    This operation is never used on an already published immutable artifact.
    """
    manifest, config, descriptor = read_bundle(bundle)
    if descriptor:
        return
    with tempfile.TemporaryDirectory(
        prefix="firmware-runtime-", dir=bundle.parent
    ) as temporary:
        staging = Path(temporary)
        read_bundle(bundle, staging)
        env = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent)
            + os.pathsep
            + os.environ.get("PATH", ""),
        }
        capture_runtime(build, staging, config, base, env)
        path = staging / "runtime/launch.json"
        manifest["files"]["runtime/launch.json"] = digest(path)
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        prepared = staging / "prepared.zip"
        with zipfile.ZipFile(prepared, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in [*manifest["files"], "manifest.json"]:
                info = zipfile.ZipInfo(name, (2026, 8, 7, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, inside(staging, name).read_bytes())
        os.replace(prepared, bundle)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["describe", "run", "enrich"])
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--zephyr-base", type=Path, required=True)
    p.add_argument("--build-dir", type=Path)
    p.add_argument("--contract", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    init_twister(a.zephyr_base)
    if a.operation == "enrich":
        enrich(a.bundle, a.build_dir, a.zephyr_base)
    elif a.operation == "describe":
        print(json.dumps(describe(a.bundle, a.zephyr_base), sort_keys=True))
    else:
        result = run(
            a.bundle, json.loads(a.contract.read_text()), a.zephyr_base, a.output
        )
        (a.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
