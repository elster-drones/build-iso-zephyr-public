import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('builder', Path(__file__).parents[1] / 'tools/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InputTests(unittest.TestCase):
    def test_traversal_and_symlink_escape(self):
        with self.assertRaises(ValueError):
            builder.inside(Path('/tmp/zephyr'), '../outside')
        with self.assertRaises(ValueError):
            builder.inside(Path('/tmp/zephyr'), '/etc/passwd')

    def test_exact_supported_boards(self):
        self.assertGreater(len(builder.TARGETS), 2000)
        self.assertEqual(builder.TARGET_REGISTRY['zephyr_revision'], builder.REVISION)
        for board in ('qemu_x86_64', 'nrf52840dk/nrf52840', 'esp32_devkitc/esp32/procpu'):
            self.assertTrue(builder.TARGETS[board]['buildable'])
            self.assertEqual(builder.TARGETS[board]['default_application'], 'apps/playground')
        for board in ('qemu_riscv32', 'qemu_riscv64', 'qemu_cortex_a53', 'rpi_pico/rp2040'):
            self.assertTrue(builder.TARGETS[board]['buildable'])
            self.assertEqual(builder.TARGETS[board]['default_application'], 'samples/hello_world')
