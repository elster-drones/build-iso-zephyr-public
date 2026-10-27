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


def bundle(tmp_path, change=None, board="96b_wistrio/stm32l151xba", test=None):
    files = {
        "zephyr/zephyr.elf": b"exact delivered executable",
        "build-config.json": json.dumps(
            {"board": board, "application": "config/app"}
        ).encode(),
    }
    if test:
        files["runtime/launch.json"] = json.dumps(
            {"board": board, "zephyr_revision": runtime.REVISION, "test": test}
        ).encode()
    manifest = {
        "zephyr_revision": runtime.REVISION,
        "board": board,
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


def test_enrichment_preserves_delivered_executable_bytes(tmp_path):
    path = bundle(tmp_path)
    before, config, _ = runtime.read_bundle(path)
    runtime.enrich(path, tmp_path / 'build', tmp_path)
    after, current_config, descriptor = runtime.read_bundle(path)
    assert after['files']['zephyr/zephyr.elf'] == before['files']['zephyr/zephyr.elf']
    assert current_config == config
    assert descriptor['board'] == before['board']
    first_digest = runtime.digest(path)
    runtime.enrich(path, tmp_path / 'build', tmp_path)
    assert runtime.digest(path) == first_digest


def test_nrf52840dk_console_test_uses_its_exact_board_renode_profile(tmp_path):
    test = {"id": "sample.basic.helloworld", "harness": "console", "harness_config": {}, "timeout": 60}
    contract = runtime.describe(bundle(tmp_path, board="nrf52840dk/nrf52840", test=test), tmp_path)
    assert contract["blocked_reason"] is None
    assert contract["launches"] == [
        {"name": "renode", "exec": "renode", "profile": "nrf52840dk/nrf52840"}
    ]


def test_renode_profile_never_claims_interactive_application_tests(tmp_path):
    test = {"id": "openfactory.playground.runtime", "harness": "application", "script": "tools/smoke.py", "suite": "playground", "timeout": 150}
    path = bundle(tmp_path, board="nrf52840dk/nrf52840", test=test)
    contract = runtime.describe(path, tmp_path)
    assert "interactive application tests are unavailable" in contract["blocked_reason"]
    report = runtime.run(path, contract, tmp_path, tmp_path / "evidence")
    assert report["status"] == "error" and report["assertions"] == []


def test_renode_model_corrections_belong_to_their_own_board_profile(tmp_path, monkeypatch):
    emulator = tmp_path / "renode"
    for model in ("platforms/cpus/stm32l151.repl", "platforms/boards/nrf52840dk_nrf52840.repl"):
        (emulator / model).parent.mkdir(parents=True, exist_ok=True)
        (emulator / model).write_text(
            'using "platforms/cpus/base.repl"\n        Tag <0x40023800 4> "RCC_CR" 0x2020202\n'
        )
    (emulator / "renode").write_text("#!/bin/sh\necho Renode v1.17.0\n")
    (emulator / "renode").chmod(0o755)
    monkeypatch.setenv("PATH", str(emulator))
    nordic, wistrio = tmp_path / "nordic", tmp_path / "wistrio"
    for output in (nordic, wistrio):
        output.mkdir()
    runtime.renode_command(runtime.PROFILES["nrf52840dk/nrf52840"], tmp_path, nordic, tmp_path)
    assert (nordic / "platform.repl").read_text() == (
        emulator / "platforms/boards/nrf52840dk_nrf52840.repl"
    ).read_text()
    assert "sysbus.uart0 CreateFileBackend" in (nordic / "boot.resc").read_text()
    runtime.renode_command(runtime.PROFILES["96b_wistrio/stm32l151xba"], tmp_path, wistrio, tmp_path)
    model = (wistrio / "platform.repl").read_text()
    assert '"RCC_CR"' not in model
    assert f'rccCr: Python.PythonPeripheral @ sysbus 0x40023800\n    size: 0x4\n    initable: true\n    filename: "{runtime.ROOT}/runtime/wistrio/rcc_cr.py"' in model
