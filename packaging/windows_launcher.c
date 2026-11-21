#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, wchar_t *arguments, int show) {
    wchar_t program[32768];
    DWORD size = GetModuleFileNameW(NULL, program, 32768);
    if (!size || size >= 32768) return 2;
    wchar_t *end = wcsrchr(program, L'\\');
    if (!end || (end - program) > 32730) return 2;
    wcscpy(end, L"\\python\\python.exe");
    size_t n = wcslen(program) + wcslen(arguments) + 64;
    wchar_t *command = calloc(n, sizeof(wchar_t));
    if (!command) return 3;
    swprintf(command, n, L"\"%ls\" -X utf8 -I -m novel_tts %ls", program, arguments);
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    BOOL started = CreateProcessW(program, command, NULL, NULL, TRUE, CREATE_NO_WINDOW,
                                  NULL, NULL, &startup, &process);
    free(command);
    if (!started) {
        MessageBoxW(NULL, L"Please extract the complete NovelTTSReader directory.", L"NovelTTSReader", MB_OK | MB_ICONERROR);
        return 4;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD result = 1;
    GetExitCodeProcess(process.hProcess, &result);
    CloseHandle(process.hProcess);
    return (int)result;
}
