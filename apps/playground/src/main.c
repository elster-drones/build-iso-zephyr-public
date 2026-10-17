#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/fs/fs.h>
#include <zephyr/fs/littlefs.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/atomic.h>

FS_LITTLEFS_DECLARE_DEFAULT_CONFIG(playground_fs);
static struct fs_mount_t mount = {
	.type = FS_LITTLEFS,
	.fs_data = &playground_fs,
	.storage_dev = "PLAYGROUND",
	.mnt_point = "/demo",
	.flags = FS_MOUNT_FLAG_USE_DISK_ACCESS,
};

static const unsigned char readme[] = {
#include <README.txt.inc>
};
static const unsigned char source[] = {
#include <main.c.inc>
};
static const unsigned char configuration[] = {
#include <prj.conf.inc>
};
static atomic_t paused;
static atomic_t samples;
static atomic_t reset_requested;

static int write_file(const char *path, const void *data, size_t size, bool append)
{
	struct fs_file_t file;
	fs_file_t_init(&file);
	int ret = fs_open(&file, path, FS_O_CREATE | FS_O_WRITE |
			  (append ? FS_O_APPEND : FS_O_TRUNC));
	if (ret < 0) {
		return ret;
	}
	ssize_t written = fs_write(&file, data, size);
	ret = fs_close(&file);
	return written < 0 ? written : written != size ? -EIO : ret;
}

static int demo_command(const struct shell *sh, size_t argc, char **argv)
{
	if (argc == 1 || strcmp(argv[1], "status") == 0) {
		int count = atomic_get(&samples);
		shell_print(sh, "Simulated sensor: %s, samples=%d, temperature=%d.%d C",
			    atomic_get(&paused) ? "paused" : "running", count,
			    22 + (count % 20) / 10, count % 10);
		shell_print(sh, "Log: /demo/data/readings.csv; source: /demo/src/main.c");
	} else if (strcmp(argv[1], "pause") == 0) {
		atomic_set(&paused, 1);
		shell_print(sh, "Demo paused");
	} else if (strcmp(argv[1], "resume") == 0) {
		atomic_clear(&paused);
		shell_print(sh, "Demo running");
	} else if (strcmp(argv[1], "reset") == 0) {
		atomic_set(&reset_requested, 1);
		shell_print(sh, "Demo reset requested");
	} else {
		shell_error(sh, "Use demo [status|pause|resume|reset]");
		return -EINVAL;
	}
	return 0;
}
SHELL_CMD_ARG_REGISTER(demo, NULL, "Simulated sensor: status, pause, resume, reset.",
		       demo_command, 1, 1);

int main(void)
{
	k_thread_name_set(k_current_get(), "sample_sensor");
	int ret = fs_mount(&mount);
	if (ret < 0) {
		printk("Playground filesystem failed: %d\n", ret);
		return ret;
	}
	ret = fs_mkdir("/demo/src");
	if (ret == 0) {
		ret = fs_mkdir("/demo/data");
	}
	if (ret == 0) {
		ret = write_file("/demo/README.txt", readme, sizeof(readme), false);
	}
	if (ret == 0) {
		ret = write_file("/demo/src/main.c", source, sizeof(source), false);
	}
	if (ret == 0) {
		ret = write_file("/demo/prj.conf", configuration, sizeof(configuration), false);
	}
	if (ret < 0) {
		printk("Playground files failed: %d\n", ret);
		return ret;
	}
	printk("\nOpenFactory Zephyr playground\n"
	       "Try: ls, cd /demo, cat README.txt, demo status, top, htop\n"
	       "The /demo RAM disk resets when the machine restarts.\n\n");
	for (;;) {
		if (atomic_cas(&reset_requested, 1, 0)) {
			atomic_clear(&samples);
		}
		int count = atomic_get(&samples);
		if (count == 0 || (!atomic_get(&paused) && count % 60 == 0)) {
			static const char header[] = "sample,uptime_ms,simulated_temperature_c\n";
			ret = write_file("/demo/data/readings.csv", header, sizeof(header) - 1, false);
		}
		if (ret == 0 && !atomic_get(&paused)) {
			char row[64];
			count = atomic_inc(&samples) + 1;
			int size = snprintf(row, sizeof(row), "%d,%lld,%d.%d\n", count,
					    (long long)k_uptime_get(), 22 + (count % 20) / 10,
					    count % 10);
			ret = write_file("/demo/data/readings.csv", row, size, true);
		}
		if (ret < 0) {
			printk("Demo log write failed: %d\n", ret);
			return ret;
		}
		k_sleep(K_SECONDS(1));
	}
}
