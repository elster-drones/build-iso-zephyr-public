# OpenFactory Zephyr builder

Native Zephyr RTOS firmware, using pinned upstream West/CMake and SDK tooling.
The repository follows OpenFactory's build-iso naming convention; its output is firmware.

Run `./build.sh` on Linux with Python 3.12+, git, a C compiler, tar, xz, and curl.
The first build installs the pinned SDK and West workspace into a shared cache.
The default application is `apps/playground`: a native shell with `ls`, `cd`,
`pwd`, `cat`, writable sample files, a simulated sensor, and live `top`/`htop`
thread monitors. Start with `ls`, `cd /demo`, `cat README.txt`, and `demo status`.
Use `top --once` for a snapshot; exit a live monitor with `q` then Enter or Ctrl+C.
The 64 KiB RAM filesystem survives terminal reconnects and resets on restart.
Source and configuration are readable inside `/demo` and included in the bundle.

Select an exact target from `targets.v1.json` (or the OpenFactory Hardware Catalog).
The generated registry preserves qualifiers, revisions, aliases and toolchain
availability. Regenerate it with the Zephyr venv using `python tools/targets.py
/path/to/pinned/zephyr`. The builder revalidates the target against pinned upstream
West/Twister discovery and provisions the upstream GNU SDK toolchains once per cache.
The original three targets retain `apps/playground`; new targets default to
`samples/hello_world`. `sysbuild` can explicitly select upstream multi-domain builds. Use upstream `samples/...` or put a custom Zephyr
application in `app/` and select `"application": "app"`. Configuration fragments
and devicetree overlays are paths relative to this checkout. Build outputs are
isolated in `build/` and `output/`. `JOBS` defaults to 4.

`output/zephyr-firmware.zip` contains native outputs, runner metadata, source
configuration, frozen dependencies, and integrity information. Successful
compilation does not certify physical hardware operation. Flash from the matching
West workspace using the upstream runner and its generated image offsets.
