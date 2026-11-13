// macOS: the "Remote Screen" app's program. Starts server.py with the app's Python and waits for it, so macOS
// asks for (and lists) Screen Recording, Accessibility and Microphone under this app's name. Paths are relative
// to the app: <folder>/Remote Screen.app/Contents/MacOS/RemoteScreen -> <folder>/.venv/bin/python <folder>/server.py
#include <libgen.h>
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
static pid_t child;
static void pass(int sig) { if (child > 0) kill(child, sig); }

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], dir[PATH_MAX], py[PATH_MAX], srv[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) || !realpath(exe, real)) return 1;
    strlcpy(dir, real, sizeof dir);
    for (int i = 0; i < 4; i++) strlcpy(dir, dirname(dir), sizeof dir);   // MacOS, Contents, .app, folder
    snprintf(py, sizeof py, "%s/.venv/bin/python", dir);
    snprintf(srv, sizeof srv, "%s/server.py", dir);
    if (chdir(dir)) return 1;
    signal(SIGTERM, pass); signal(SIGINT, pass); signal(SIGHUP, pass);
    char *argv[] = {py, srv, NULL};
    if (posix_spawn(&child, py, NULL, NULL, argv, environ)) { perror("spawn"); return 1; }
    int st;
    while (waitpid(child, &st, 0) < 0) {}
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
