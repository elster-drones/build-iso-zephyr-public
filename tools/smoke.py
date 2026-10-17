"""Boot the upstream shell and retain its real serial evidence."""
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

images = Path(sys.argv[1]).resolve()
process = subprocess.Popen([
    'qemu-system-x86_64', '-machine', 'q35', '-cpu', 'qemu64,+x2apic',
    '-m', '256', '-smp', '2', '-display', 'none', '-monitor', 'none',
    '-serial', 'stdio', '-kernel', str(images / 'zephyr-qemu-locore.elf'),
    '-device', f'loader,file={images}/zephyr-qemu-main.elf',
], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
selector = selectors.DefaultSelector()
selector.register(process.stdout, selectors.EVENT_READ)
output = b''
sent = False
deadline = time.monotonic() + 30
try:
    while time.monotonic() < deadline:
        for key, _ in selector.select(.2):
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                raise RuntimeError('QEMU exited before shell verification')
            output += data
        if not sent and b'uart:' in output:
            process.stdin.write(b'help\rversion\r')
            process.stdin.flush()
            sent = True
        if sent and b'Zephyr version 4.4.2' in output and b'Available commands:' in output:
            break
    else:
        raise RuntimeError('Zephyr shell did not pass its boot and command checks')
    print('Zephyr 4.4.2: QEMU boot, help and version passed')
finally:
    (images.parent / 'serial-evidence.log').write_bytes(output)
    process.terminate()
    process.wait(timeout=5)
