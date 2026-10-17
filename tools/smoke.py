"""Exercise the real playground firmware over QEMU serial; retain all output."""
import os
from pathlib import Path
import re
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
output = bytearray()
ansi = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def read_until(check, timeout=20):
    chunk = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for key, _ in selector.select(.1):
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                raise RuntimeError('QEMU exited before verification')
            output.extend(data)
            chunk.extend(data)
        text = ansi.sub(b'', chunk).decode(errors='replace')
        if check(text):
            return text
    raise AssertionError('Serial check timed out:\n' + chunk.decode(errors='replace'))


def send(data):
    process.stdin.write(data)
    process.stdin.flush()


def command(text, expected):
    send(text.encode() + b'\r')
    result = read_until(lambda value: value.rstrip().endswith('uart:~$'))
    assert expected in result, (text, expected, result)
    assert 'command not found' not in result and 'FATAL ERROR' not in result, result
    return result


try:
    read_until(lambda value: 'OpenFactory Zephyr playground' in value)
    send(b'\r')
    read_until(lambda value: value.rstrip().endswith('uart:~$'))
    command('help', 'Available commands:')
    command('version', 'Zephyr version 4.4.2')
    command('ls', 'demo/')
    command('cd /demo', 'uart:~$')
    command('pwd', '/demo')
    command('cat README.txt', 'Welcome to the OpenFactory Zephyr playground!')
    command('ls src', 'main.c')
    command('cat src/main.c', 'Simulated sensor:')
    command('cat prj.conf', 'CONFIG_THREAD_RUNTIME_STATS=y')
    command('cat data/readings.csv', 'simulated_temperature_c')
    command('mkdir scratch', 'uart:~$')
    command('fs write scratch/note.txt 48 65 6c 6c 6f 0a', 'uart:~$')
    command('cat scratch/note.txt', 'Hello')
    command('cp scratch/note.txt scratch/copy.txt', 'uart:~$')
    command('mv scratch/copy.txt scratch/moved.txt', 'uart:~$')
    command('cat scratch/moved.txt', 'Hello')
    command('rm scratch/moved.txt', 'uart:~$')
    command('demo pause', 'Demo paused')
    time.sleep(2)
    before = command('demo status', 'paused')
    time.sleep(2)
    after = command('demo status', 'paused')
    assert re.search(r'samples=(\d+)', before)[1] == re.search(r'samples=(\d+)', after)[1]
    command('demo reset', 'reset requested')
    time.sleep(2)
    command('demo status', 'samples=0')
    command('demo resume', 'Demo running')
    time.sleep(2)
    result = command('demo status', 'running')
    assert int(re.search(r'samples=(\d+)', result)[1]) > 0
    command('ps', 'sample_sensor')
    command('uptime', 'Uptime:')
    for name, exit_key in [('top', b'q\r'), ('htop', b'\x03')]:
        snapshot = command(name + ' --once', 'STACK used/total')
        assert re.search(r'sample_sensor\s+\d+%\s+\d+\s+\d+/\d+', snapshot), snapshot
        send(name.encode() + b'\r')
        live = read_until(lambda value: value.count('STACK used/total') >= 2)
        assert 'sample_sensor' in live
        send(exit_key)
        read_until(lambda value: 'Monitor stopped' in value and value.rstrip().endswith('uart:~$'))
        command('ls', 'README.txt')
    command('cd ..', 'uart:~$')
    command('pwd', '/')
    command('ls /demo', 'README.txt')
    print('Zephyr playground: browsing, writable files, sample app, top/htop refresh and exit passed')
finally:
    (images.parent / 'serial-evidence.log').write_bytes(output)
    process.terminate()
    process.wait(timeout=5)
