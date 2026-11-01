/* Fixed Windows AppContainer IPC probe. Synthetic PROBE_PING only, NOT a worker. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <wchar.h>

#define MAX_BODY 2048U
#define DIGEST "6ceca15c73a4beb7c9c64c81d8fa1b5abed3ebc44f7b03e9c1749a8e2a0eee2e"

static int read_exact(HANDLE handle, unsigned char *bytes, DWORD length) {
    DWORD got = 0;
    while (length > 0) {
        DWORD n = 0;
        if (!ReadFile(handle, bytes + got, length, &n, NULL) || n == 0)
            return 0;
        got += n;
        length -= n;
    }
    return 1;
}

static int write_exact(HANDLE handle, const unsigned char *bytes, DWORD length) {
    DWORD sent = 0;
    while (sent < length) {
        DWORD n = 0;
        if (!WriteFile(handle, bytes + sent, length - sent, &n, NULL) || n == 0)
            return 0;
        sent += n;
    }
    return 1;
}

/* Extract only fixed lowercase hex identities from canonical, manager-validated input. */
static int extract(const char *body, const char *key, const char *prefix,
                   char *dest, size_t len) {
    const char *found = strstr(body, key);
    size_t i;
    if (!found) return 0;
    found += strlen(key);
    if (strncmp(found, prefix, strlen(prefix)) != 0) return 0;
    found += strlen(prefix);
    for (i = 0; i < len; ++i) {
        char ch = found[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
            return 0;
        dest[i] = ch;
    }
    dest[len] = '\0';
    return found[len] == '"';
}

int wmain(int argc, wchar_t **argv) {
    /* Developer-owned CI scenarios only. No worker-facing API or commands. */
    const wchar_t *mode = (argc == 1) ? L"ok" : ((argc == 2) ? argv[1] : L"invalid");
    if (wcscmp(mode, L"ok") && wcscmp(mode, L"crash") &&
        wcscmp(mode, L"hang") && wcscmp(mode, L"truncated") &&
        wcscmp(mode, L"oversized") && wcscmp(mode, L"wrong_nonce") &&
        wcscmp(mode, L"trailing")) return 42;
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
    unsigned char header[4], body[MAX_BODY+1];
    char job[33], attempt[33], operation[33], nonce[33];
    char reply[MAX_BODY+1];
    unsigned char frame[4+MAX_BODY];
    DWORD size;
    int count;
    if (!input || input == INVALID_HANDLE_VALUE ||
        !output || output == INVALID_HANDLE_VALUE) return 50;
    if (!read_exact(input, header, 4)) return 51;
    size = ((DWORD)header[0] << 24) | ((DWORD)header[1] << 16) |
           ((DWORD)header[2] << 8) | (DWORD)header[3];
    if (size < 1 || size > MAX_BODY) return 52;
    if (!read_exact(input, body, size)) return 53;
    body[size] = '\0';
    if (!extract((char *)body, "\"job_id\":\"", "JOB-", job, 32) ||
        !extract((char *)body, "\"attempt_id\":\"", "ATT-", attempt, 32) ||
        !extract((char *)body, "\"operation_id\":\"", "OP-", operation, 32) ||
        !extract((char *)body, "\"nonce\":\"", "", nonce, 32) ||
        !strstr((char *)body, "\"kind\":\"request\"") ||
        !strstr((char *)body, "\"capability\":\"PROBE_PING\"") ||
        !strstr((char *)body, "\"version\":1")) return 54;
    if (wcscmp(mode, L"crash") == 0) {
        TerminateProcess(GetCurrentProcess(), 77);
        return 77;
    }
    if (wcscmp(mode, L"hang") == 0) {
        Sleep(15000);
        return 78;
    }
    if (wcscmp(mode, L"wrong_nonce") == 0)
        nonce[0] = (nonce[0] == 'a') ? 'b' : 'a';
    count = snprintf(reply, sizeof(reply),
        "{\"attempt_id\":\"ATT-%s\",\"capability\":\"PROBE_PING\","
        "\"job_id\":\"JOB-%s\",\"kind\":\"response\",\"nonce\":\"%s\","
        "\"observation_sha256\":\"%s\",\"operation_id\":\"OP-%s\","
        "\"result\":\"PONG\",\"version\":1}",
        attempt, job, nonce, DIGEST, operation);
    if (count <= 0 || (unsigned)count > MAX_BODY) return 55;
    size = (DWORD)count;
    frame[0] = (unsigned char)(size >> 24);
    frame[1] = (unsigned char)(size >> 16);
    frame[2] = (unsigned char)(size >> 8);
    frame[3] = (unsigned char)size;
    memcpy(frame + 4, reply, size);
    if (wcscmp(mode, L"oversized") == 0) {
        unsigned char bad_header[4] = {0xff, 0xff, 0xff, 0xff};
        return write_exact(output, bad_header, 4) ? 0 : 58;
    }
    if (wcscmp(mode, L"truncated") == 0)
        return write_exact(output, frame, 5) ? 0 : 59;
    if (!write_exact(output, frame, size+4)) return 56;
    if (wcscmp(mode, L"trailing") == 0) {
        const unsigned char garbage = 'X';
        if (!write_exact(output, &garbage, 1)) return 60;
    }
    /* The parent reads after process exit: flushing a pipe can deadlock. */
    return 0;
}
