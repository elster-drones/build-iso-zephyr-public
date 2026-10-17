"""Generate the published target registry using pinned Zephyr/West discovery.

Run with the Zephyr venv. Twister owns aliases, qualifiers and toolchain support.
No board discovery or compiler selection is reimplemented here.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REVISION = "dccb09599635bdff17633fa7e9dab014b91dce90"
PLAYGROUND = {"qemu_x86_64", "nrf52840dk/nrf52840", "esp32_devkitc/esp32/procpu"}


def discover(base: Path) -> dict:
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base, text=True).strip() != REVISION:
        raise ValueError("Target discovery requires the pinned Zephyr revision")
    os.environ["ZEPHYR_BASE"] = str(base)
    sys.path[:0] = [str(base / p) for p in ("scripts", "scripts/pylib/twister", "scripts/pylib/python-devicetree")]
    from twisterlib.platform import generate_platforms
    from argparse import Namespace
    import list_boards
    import yaml
    import zephyr_module
    from west.manifest import Manifest
    roots = {key: [base] for key in ("board_root", "soc_root", "arch_root")}
    for module in zephyr_module.parse_modules(base, Manifest.from_topdir(base.parent)):
        for key in roots:
            root = module.meta.get("build", {}).get("settings", {}).get(key)
            if root:
                roots[key].append(Path(module.project) / root)
    boards = list_boards.find_v2_boards(Namespace(board_roots=roots["board_root"], soc_roots=roots["soc_root"], arch_roots=roots["arch_root"], board=None, board_dir=None))
    targets = {}
    for platform in generate_platforms(roots["board_root"], roots["soc_root"], roots["arch_root"]):
        board = boards[platform.name.split("/")[0].split("@")[0]]
        architecture = {"arm64": "aarch64"}.get(platform.arch, platform.arch)
        if platform.name.startswith("qemu_x86_64/"):
            architecture = "x86_64"
        reason = None
        if "zephyr" not in platform.supported_toolchains:
            reason = "Requires an upstream toolchain outside the pinned GNU SDK"
        if platform.arch not in {"arm", "arm64", "x86", "xtensa", "riscv", "arc", "mips", "openrisc", "rx", "sparc"}:
            reason = "Requires a host or vendor-specific build environment"
        # Only explicit upstream memory observations are published, never Twister defaults.
        specs = {}
        for file in board.dir.glob("*.yaml"):
            data = yaml.safe_load(file.read_text()) or {}
            if data.get("identifier") in platform.aliases:
                for field in ("ram", "flash"):
                    if isinstance(data.get(field), int):
                        specs[field + "_bytes"] = data[field] * 1024
            elif file.name == "twister.yaml":
                variant = data.get("variants", {}).get(platform.name, {})
                for field in ("ram", "flash"):
                    value = variant.get(field, data.get(field))
                    if isinstance(value, int):
                        specs[field + "_bytes"] = value * 1024
        entry = {"board": board.name, "name": board.full_name or board.name, "vendor": board.vendor or "unknown",
                 "architecture": architecture, "soc": platform.name.split("/", 1)[-1].split("/")[0],
                 "specifications": specs, "interfaces": sorted(platform.supported), "kind": "emulator" if "qemu" in board.dir.relative_to(base).parts else "development_board",
                 "buildable": reason is None, "unavailable_reason": reason, "sysbuild": platform.sysbuild,
                 "documentation": "https://github.com/zephyrproject-rtos/zephyr/tree/" + REVISION + "/" + (str(board.dir.relative_to(base)) if board.dir.is_relative_to(base) else str(board.dir.name)) + "/doc"}
        for alias in platform.aliases:
            targets[alias] = {**entry, "target": alias, "canonical_target": platform.name,
                              "default_application": "apps/playground" if alias in PLAYGROUND else "samples/hello_world"}
    # Include discovery-only targets even when upstream has no test-platform metadata.
    for board in boards.values():
        for qualifier in list_boards.board_v2_qualifiers(board):
            for revision in [None] + [r.name for r in board.revisions]:
                target = f"{board.name}{'@' + revision if revision else ''}/{qualifier}"
                if target not in targets:
                    targets[target] = {"target": target, "canonical_target": target, "board": board.name,
                        "name": board.full_name or board.name, "vendor": board.vendor or "unknown",
                        "architecture": None, "buildable": False,
                        "unavailable_reason": "No upstream platform architecture/toolchain metadata", "default_application": "samples/hello_world"}
    vendors = {}
    for line in (base / "dts/bindings/vendor-prefixes.txt").read_text().splitlines():
        if line and not line.startswith("#") and "\t" in line:
            prefix, vendor = line.split("\t", 1)
            vendors[prefix] = vendor.strip()
    components = []
    kinds = {"sensor": "sensor_family", "display": "display_controller_family", "flash": "memory_family",
             "mtd": "memory_family", "memory-controllers": "memory_family", "wifi": "radio_family", "bluetooth": "radio_family", "ieee802154": "radio_family"}
    for folder, kind in kinds.items():
        for file in sorted((base / "dts/bindings" / folder).glob("*.yaml")):
            data = yaml.safe_load(file.read_text()) or {}
            compatible = data.get("compatible")
            if not isinstance(compatible, str) or "," not in compatible:
                continue
            prefix, part = compatible.split(",", 1)
            if prefix not in vendors or prefix in {"zephyr", "jedec", "generic"}:
                continue
            components.append({"id": "binding:" + compatible, "name": compatible, "model": compatible,
                "vendor": vendors[prefix], "category": kind, "series": part, "summary": data.get("description", "").strip(),
                "source_url": "https://github.com/zephyrproject-rtos/zephyr/blob/" + REVISION + "/" + str(file.relative_to(base)),
                "operating_systems": ["zephyr"], "identification_scope": "Devicetree compatible family; not an exact manufacturer part number"})
    return {"schema_version": 1, "zephyr_version": "4.4.2", "zephyr_revision": REVISION, "sdk_version": "1.0.1",
            "targets": dict(sorted(targets.items())), "components": components, "vendors": vendors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("zephyr", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "targets.v1.json")
    args = parser.parse_args()
    args.output.write_text(json.dumps(discover(args.zephyr.resolve()), indent=2) + "\n")
