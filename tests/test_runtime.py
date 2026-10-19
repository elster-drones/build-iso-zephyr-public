import importlib.util
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    "runtime", Path(__file__).parents[1] / "tools/runtime.py"
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def bundle(tmp_path, change=None):
    files = {
        "zephyr/zephyr.elf": b"exact delivered executable",
        "build-config.json": json.dumps(
            {"board": "96b_wistrio/stm32l151xba", "application": "config/app"}
        ).encode(),
    }
    manifest = {
        "zephyr_revision": runtime.REVISION,
        "board": "96b_wistrio/stm32l151xba",
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
    }
    if change:
        change(manifest, files)
    path = tmp_path / "firmware.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("manifest.json", json.dumps(manifest))
    return path


def test_tampered_executable_is_not_an_emulator_input(tmp_path):
    path = bundle(
        tmp_path, lambda m, files: files.update({"zephyr/zephyr.elf": b"changed"})
    )
    with pytest.raises(ValueError, match="integrity"):
        runtime.read_bundle(path, tmp_path / "extracted")


def test_archive_member_cannot_escape_test_workspace(tmp_path):
    path = bundle(tmp_path, lambda m, files: files.update({"../outside": b"unsafe"}))
    with pytest.raises(ValueError, match="escapes"):
        runtime.read_bundle(path, tmp_path / "extracted")
    assert not (tmp_path / "outside").exists()


def test_missing_application_test_is_blocked_instead_of_an_empty_pass(tmp_path):
    path = bundle(tmp_path)
    contract = runtime.describe(path, tmp_path)
    assert contract["blocked_reason"]
    report = runtime.run(path, contract, tmp_path, tmp_path / "evidence")
    assert report["status"] == "error"
    assert report["assertions"] == []
    assert report["hardware_tested"] is False


def test_contract_cannot_switch_the_artifact_after_admission(tmp_path):
    path = bundle(tmp_path)
    contract = runtime.describe(path, tmp_path)
    contract["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="contract changed"):
        runtime.run(path, contract, tmp_path, tmp_path / "evidence")
