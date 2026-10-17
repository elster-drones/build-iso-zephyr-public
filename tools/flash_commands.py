"""Record upstream runner commands without executing a flasher."""
import argparse
import json
import os
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(os.environ["ZEPHYR_BASE"]) / "scripts/west_commands"))
from runners.core import RunnerConfig
from runners.esp32 import Esp32BinaryRunner

root = Path(sys.argv[1]).resolve()
domains = yaml.safe_load((root / "domains.yaml").read_text())
commands = []
for name in domains["flash_order"]:
    build = root / name
    metadata = yaml.safe_load((build / "zephyr/runners.yaml").read_text())
    config = metadata["config"]
    parser = argparse.ArgumentParser()
    Esp32BinaryRunner.add_parser(parser)
    args = parser.parse_args(metadata["args"]["esp32"])
    cfg = RunnerConfig(str(build), config["board_dir"],
        str(build / "zephyr" / config["elf_file"]), None, None,
        str(build / "zephyr" / config["bin_file"]), None, None, None)
    runner = Esp32BinaryRunner.create(cfg, args)
    runner.check_call = lambda command, **kwargs: commands.append([
        str(Path(arg).relative_to(root)) if Path(arg).is_absolute() and Path(arg).is_relative_to(root) else arg
        for arg in command])
    runner.do_run("flash")
print(json.dumps(commands))
