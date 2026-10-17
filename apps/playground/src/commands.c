/* Familiar names for Zephyr's existing filesystem and kernel commands. */
#include <errno.h>
#include <string.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/iterable_sections.h>

/* Call the registered static handler directly: shell_execute_cmd() must
 * never be called recursively from inside a shell command handler.
 */
static int native_command(const struct shell *sh, size_t argc, char **argv,
			  const char *root, const char *group, const char *name)
{
	TYPE_SECTION_FOREACH(union shell_cmd_entry, shell_root_cmds, cmd) {
		if (strcmp(cmd->entry->syntax, root) != 0) {
			continue;
		}
		const struct shell_static_entry *entry = cmd->entry;
		const char *path[] = {group, name};
		for (size_t i = 0; i < ARRAY_SIZE(path); i++) {
			if (path[i] == NULL) {
				continue;
			}
			if (entry->subcmd == NULL) {
				return -ENOENT;
			}
			/* Section command sets start with an empty descriptor. */
			entry = entry->subcmd->entry ? entry->subcmd->entry :
				(const struct shell_static_entry *)entry->subcmd + 1;
			while (entry->syntax && strcmp(entry->syntax, path[i]) != 0) {
				entry++;
			}
			if (entry->syntax == NULL) {
				return -ENOENT;
			}
		}
		return entry->handler ? entry->handler(sh, argc, argv) : -ENOENT;
	}
	return -ENOENT;
}

static int filesystem_command(const struct shell *sh, size_t argc, char **argv)
{
	return native_command(sh, argc, argv, "fs", NULL, argv[0]);
}

static int ps_command(const struct shell *sh, size_t argc, char **argv)
{
	return native_command(sh, argc, argv, "kernel", "thread", "list");
}

static int uptime_command(const struct shell *sh, size_t argc, char **argv)
{
	return native_command(sh, argc, argv, "kernel", NULL, "uptime");
}

static int version_command(const struct shell *sh, size_t argc, char **argv)
{
	return native_command(sh, argc, argv, "kernel", NULL, "version");
}

SHELL_CMD_ARG_REGISTER(ls, NULL, "List files [path].", filesystem_command, 1, 1);
SHELL_CMD_ARG_REGISTER(cd, NULL, "Change directory [path].", filesystem_command, 1, 1);
SHELL_CMD_ARG_REGISTER(pwd, NULL, "Print working directory.", filesystem_command, 1, 0);
SHELL_CMD_ARG_REGISTER(cat, NULL, "Read text files <path> [...].", filesystem_command, 2, 8);
SHELL_CMD_ARG_REGISTER(mkdir, NULL, "Create directory <path>.", filesystem_command, 2, 0);
SHELL_CMD_ARG_REGISTER(cp, NULL, "Copy file <source> <dest>.", filesystem_command, 3, 0);
SHELL_CMD_ARG_REGISTER(mv, NULL, "Move file <source> <dest>.", filesystem_command, 3, 0);
SHELL_CMD_ARG_REGISTER(rm, NULL, "Remove file or directory <path>.", filesystem_command, 2, 0);
SHELL_CMD_ARG_REGISTER(ps, NULL, "List Zephyr threads and runtime statistics.", ps_command, 1, 0);
SHELL_CMD_ARG_REGISTER(uptime, NULL, "Show time since boot.", uptime_command, 1, 0);
SHELL_CMD_ARG_REGISTER(version, NULL, "Show Zephyr version.", version_command, 1, 0);
