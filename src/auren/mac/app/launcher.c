// Auren.app's executable: it runs Auren's Python as a child and waits for it.
//
//   Auren.app/Contents/MacOS/Auren daemon    what com.auren.daemon runs   (python -m auren.cli daemon)
//   Auren.app/Contents/MacOS/Auren mascot    what com.auren.mascot runs
//   Auren.app/Contents/MacOS/Auren           opened from Finder or Spotlight: `auren open`
//
// macOS gives a permission (microphone, Accessibility, Screen Recording) to the app responsible for a
// process, and a child stays the responsibility of the app that started it. So Python asks as "Auren",
// and Settings lists Auren, with its icon, not a python3.12 in a hidden folder. Python can then be
// updated or replaced without losing the permissions.
//
// Keep this file, Info.plist and the icon unchanged when you can: a new build is a new signature, and
// macOS asks for the permissions again.
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static volatile pid_t child = 0;
static volatile sig_atomic_t early = 0;     // a signal from before Python started

static void forward(int sig) {
    if (child > 0) kill(child, sig);
    else early = sig;
}

static void alert(const char *python) {
    char script[PATH_MAX + 256];
    snprintf(script, sizeof script,
             "display alert \"Auren isn't installed\" message \"There's no Python at %s. "
             "Run ./install-macos.sh in Auren's folder.\"", python);
    char *args[] = {"/usr/bin/osascript", "-e", script, NULL};
    pid_t pid;
    if (posix_spawn(&pid, args[0], NULL, NULL, args, environ) == 0) waitpid(pid, NULL, 0);
}

int main(int argc, char **argv) {
    // The bundle's path, for Python to name the app (AUREN_APP)
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) == 0 && realpath(exe, real)) {
        char *end = strstr(real, "/Contents/MacOS/");
        if (end) {
            *end = '\0';
            setenv("AUREN_APP", real, 1);
        }
    }

    // Auren's Python: AUREN_PYTHON (the launch agents set it), else next to the auren command the
    // installer linked into ~/.local/bin (wherever its data folder is), else the default place
    char fallback[PATH_MAX], link[PATH_MAX], target[PATH_MAX];
    const char *python = getenv("AUREN_PYTHON");
    if (!python || !*python) {
        const char *home = getenv("HOME");
        if (!home) return 1;
        snprintf(fallback, sizeof fallback, "%s/.local/share/auren/venv/bin/python", home);
        snprintf(link, sizeof link, "%s/.local/bin/auren", home);
        char *slash;
        if (access(fallback, X_OK) != 0 && realpath(link, target) && (slash = strrchr(target, '/'))) {
            *slash = '\0';
            if (strlen(target) + sizeof "/python" <= sizeof fallback) {
                strcpy(fallback, target);
                strcat(fallback, "/python");
            }
        }
        python = fallback;
    }
    if (access(python, X_OK) != 0) {
        alert(python);
        return 1;
    }

    char **args = calloc(argc + 5, sizeof(char *));
    if (!args) return 1;
    int n = 0;
    args[n++] = (char *)python;
    args[n++] = "-P";                     // not the current folder on sys.path
    args[n++] = "-m";
    args[n++] = "auren.cli";
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0) args[n++] = argv[i];   // old Finder launches add a process serial number
    if (n == 4) args[n++] = "open";
    args[n] = NULL;

    struct sigaction sa = {0};
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2};
    for (size_t i = 0; i < sizeof signals / sizeof *signals; i++) sigaction(signals[i], &sa, NULL);

    pid_t pid;
    int err = posix_spawn(&pid, python, NULL, NULL, args, environ);
    if (err != 0) {
        fprintf(stderr, "Auren: can't start %s: %s\n", python, strerror(err));
        return 1;
    }
    child = pid;
    if (early) kill(pid, early);          // launchd asked to stop while Python was starting
    int status;
    while (waitpid(pid, &status, 0) < 0)
        if (errno != EINTR) return 1;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
