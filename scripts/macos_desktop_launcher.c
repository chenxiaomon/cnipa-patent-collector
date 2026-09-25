/* Keep a native LaunchServices entry while running the existing project Python. */
#include <CoreFoundation/CoreFoundation.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    CFTypeRef project_directory = CFBundleGetValueForInfoDictionaryKey(
        CFBundleGetMainBundle(), CFSTR("CNIPAProjectDirectory"));
    char project_path[PATH_MAX];
    if (!project_directory || CFGetTypeID(project_directory) != CFStringGetTypeID() ||
        !CFStringGetFileSystemRepresentation(project_directory, project_path, sizeof(project_path))) {
        fputs("CNIPA desktop entry has no valid project directory. Reinstall the desktop icon.\n", stderr);
        return 1;
    }
    if (chdir(project_path) != 0) {
        perror("CNIPA project directory");
        return 1;
    }
    char **python_arguments = calloc((size_t)argc + 2, sizeof(char *));
    if (!python_arguments) {
        perror("CNIPA desktop arguments");
        return 1;
    }
    python_arguments[0] = "./.venv/bin/python";
    python_arguments[1] = "./desktop_dashboard.py";
    int argument_count = 2;
    for (int index = 1; index < argc; ++index) {
        /* Older LaunchServices versions pass a process serial number, not a CLI argument. */
        if (strncmp(argv[index], "-psn_", 5) != 0) {
            python_arguments[argument_count++] = argv[index];
        }
    }
    execv(python_arguments[0], python_arguments);
    perror("CNIPA project Python");
    free(python_arguments);
    return 1;
}
