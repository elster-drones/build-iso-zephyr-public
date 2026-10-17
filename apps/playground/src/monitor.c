/* Native Zephyr thread monitors; both commands share one renderer. */
#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>

struct thread_sample {
	const struct k_thread *thread;
	uint64_t cycles;
};

struct monitor {
	struct thread_sample previous[32];
	uint64_t total;
	uint64_t elapsed;
	const struct shell *shell;
	bool render;
	bool color;
	bool truncated;
};

static void sample_thread(const struct k_thread *thread, void *data)
{
	struct monitor *monitor = data;
	k_thread_runtime_stats_t stats;
	if (k_thread_runtime_stats_get((k_tid_t)thread, &stats) < 0) {
		return;
	}
	size_t index;
	for (index = 0; index < ARRAY_SIZE(monitor->previous); index++) {
		if (monitor->previous[index].thread == thread ||
		    monitor->previous[index].thread == NULL) {
			break;
		}
	}
	if (index == ARRAY_SIZE(monitor->previous)) {
		monitor->truncated = true;
		return;
	}
	uint64_t delta = stats.execution_cycles - monitor->previous[index].cycles;
	monitor->previous[index] = (struct thread_sample){thread, stats.execution_cycles};
	if (!monitor->render) {
		return;
	}
	unsigned int percent = monitor->elapsed ? MIN(100ULL, delta * 100 / monitor->elapsed) : 0;
	size_t unused;
	char stack[32] = "unavailable";
	char state[32];
	const char *name = k_thread_name_get((k_tid_t)thread);
	if (k_thread_stack_space_get(thread, &unused) == 0) {
		snprintk(stack, sizeof(stack), "%zu/%zu", thread->stack_info.size - unused,
			 thread->stack_info.size);
	}
	shell_print(monitor->shell, "%s%-18.18s %5u%% %6d %-16s %s%s",
		    monitor->color && percent > 0 ? "\033[32m" : "", name ? name : "unnamed",
		    percent, k_thread_priority_get((k_tid_t)thread), stack,
		    k_thread_state_str((k_tid_t)thread, state, sizeof(state)),
		    monitor->color ? "\033[0m" : "");
}

static int monitor_command(const struct shell *sh, size_t argc, char **argv)
{
	bool once = argc == 2 && strcmp(argv[1], "--once") == 0;
	if (argc == 2 && !once) {
		shell_error(sh, "Use %s [--once]", argv[0]);
		return -EINVAL;
	}
	struct monitor monitor = {.shell = sh, .color = strcmp(argv[0], "htop") == 0};
	k_thread_runtime_stats_t stats;
	if (k_thread_runtime_stats_all_get(&stats) < 0) {
		return -ENOTSUP;
	}
	monitor.total = stats.execution_cycles;
	k_thread_foreach_unlocked(sample_thread, &monitor);
	if (!once) {
		shell_print(sh, "\033[?25l");
	}
	int ret = 0;
	for (;;) {
		/* Let sleeping threads accumulate real runtime between samples. */
		k_sleep(K_SECONDS(1));
		if (k_thread_runtime_stats_all_get(&stats) < 0) {
			ret = -EIO;
			break;
		}
		monitor.elapsed = stats.execution_cycles - monitor.total;
		monitor.total = stats.execution_cycles;
		monitor.render = true;
		if (!once) {
			shell_fprintf(sh, SHELL_NORMAL, "\033[H\033[2J");
		}
		shell_print(sh, "%sZephyr %s | uptime %lld ms | q + Enter or Ctrl+C to quit%s",
			    monitor.color ? "\033[1;36m" : "", argv[0], (long long)k_uptime_get(),
			    monitor.color ? "\033[0m" : "");
		shell_print(sh, "THREAD              CPU%%    PRI STACK used/total STATE");
		k_thread_foreach_unlocked(sample_thread, &monitor);
		if (monitor.truncated) {
			shell_print(sh, "Showing first %zu threads", ARRAY_SIZE(monitor.previous));
		}
		if (once) {
			break;
		}
		uint8_t input[16];
		int count = shell_readline(sh, input, sizeof(input), K_SECONDS(1));
		if (count == -ECANCELED || (count > 0 && input[0] == 'q')) {
			break;
		}
		if (count < 0 && count != -ETIMEDOUT && count != -ENOBUFS) {
			ret = count;
			break;
		}
	}
	if (!once) {
		shell_print(sh, "\033[0m\033[?25hMonitor stopped");
	}
	return ret;
}

SHELL_CMD_ARG_REGISTER(top, NULL, "Live Zephyr thread monitor [--once]. Quit: q + Enter or Ctrl+C.",
		       monitor_command, 1, 1);
SHELL_CMD_ARG_REGISTER(htop, NULL, "Colored Zephyr thread monitor [--once]. Quit: q + Enter or Ctrl+C.",
		       monitor_command, 1, 1);
