# OpenFactory Zephyr builder

Native Zephyr RTOS firmware, using pinned upstream West/CMake and SDK tooling.
The repository follows OpenFactory's build-iso naming convention; its output is firmware.

Run `./build.sh` on Linux with Python 3.12+, git, a C compiler, tar, xz, and curl.
The first build installs the pinned SDK and West workspace into a shared cache.
Change `config/zephyr.json` to select `qemu_x86_64`, `nrf52840dk/nrf52840`, or
`esp32_devkitc/esp32/procpu`. Use upstream `samples/...` or put a custom Zephyr
application in `app/` and select `"application": "app"`. Configuration fragments
and devicetree overlays are paths relative to this checkout. Build outputs are
isolated in `build/` and `output/`. `JOBS` defaults to 4.

`output/zephyr-firmware.zip` contains native outputs, runner metadata, source
configuration, frozen dependencies, and integrity information. Successful
compilation does not certify physical hardware operation. Flash from the matching
West workspace using the upstream runner and its generated image offsets.
