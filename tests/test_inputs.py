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
        self.assertEqual(set(builder.BOARDS), {'qemu_x86_64', 'nrf52840dk/nrf52840', 'esp32_devkitc/esp32/procpu'})
