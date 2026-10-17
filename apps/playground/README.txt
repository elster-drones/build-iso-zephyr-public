Welcome to the OpenFactory Zephyr playground!

This is Zephyr RTOS running a native application. Use Tab for command
completion, the arrow keys for history, and help to discover commands.

Explore the files:
  ls
  cd /demo
  pwd
  cat README.txt
  ls src
  cat src/main.c
  cat prj.conf
  cat data/readings.csv

Try the sample application:
  demo status
  demo pause
  demo resume
  demo reset

The sample thread takes one SIMULATED temperature reading each second.
Its last 60 samples are kept in data/readings.csv, then the log starts over.
No physical sensor is attached. Pause the demo to keep the log still.
The application source and configuration above are the files compiled into
this firmware; the complete project is in the downloadable firmware bundle.

System tools:
  top                 live thread CPU and stack monitor
  htop                the same monitor with terminal colors
  top --once          print one snapshot and return
  ps                  detailed native Zephyr thread listing
  uptime              time since boot
  version             Zephyr version
  kernel thread stacks
  device list
  clear

In top or htop, type q then Enter or press Ctrl+C to return to the shell.
CPU percentages measure thread execution during each refresh interval.
Stack usage is a high-water mark in bytes, not Linux process memory.

ls, cd, pwd, cat, mkdir, cp, mv and rm use Zephyr's own fs commands.
The full fs command group also provides read, write, trunc and statvfs.
Example: fs write /demo/note.txt 48 65 6c 6c 6f 0a
Then: cat /demo/note.txt

Files live on a writable 64 KiB RAM disk mounted at /demo. They survive
terminal reconnects and reset when the machine restarts. Zephyr does not
provide Linux packages, /proc, Bash pipelines, or desktop applications.
