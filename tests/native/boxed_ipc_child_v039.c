/* Fixed Windows AppContainer IPC probe. Synthetic PROBE_PING only, NOT a worker. */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <wchar.h>
#include <stdlib.h>
#pragma comment(lib, "ws2_32.lib")

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


/* All v0.41 scenarios are fixed trusted-CI probes, never worker commands. */
static int deny_inherited_event(const wchar_t *number) {
    wchar_t *end = NULL;
    unsigned long long raw = wcstoull(number, &end, 10);
    DWORD flags = 0;
    HANDLE candidate;
    if (raw == 0 || !end || *end != L'\0' || (unsigned long long)(ULONG_PTR)raw != raw)
        return 80;
    candidate = (HANDLE)(ULONG_PTR)raw;
    SetLastError(0);
    if (GetHandleInformation(candidate, &flags)) return 81;
    if (GetLastError() != ERROR_INVALID_HANDLE) return 82;
    SetLastError(0);
    if (WaitForSingleObject(candidate, 0) != WAIT_FAILED) return 83;
    if (GetLastError() != ERROR_INVALID_HANDLE) return 84;
    return 0;
}

static int deny_job_breakaway(void) {
    BOOL inside = FALSE;
    wchar_t self[32768], cmd[32768];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD last;
    DWORD n = GetModuleFileNameW(NULL, self, 32768);
    if (!IsProcessInJob(GetCurrentProcess(), NULL, &inside) || !inside) return 85;
    if (n == 0 || n > 32000) return 86;
    if (_snwprintf_s(cmd, 32768, _TRUNCATE, L"\"%s\" forbidden-grandchild", self) < 0)
        return 87;
    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);
    if (CreateProcessW(self, cmd, NULL, NULL, FALSE,
                       CREATE_SUSPENDED | CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW,
                       NULL, NULL, &si, &pi)) {
        /* Do not permit the fixed negative-control grandchild to run. */
        TerminateProcess(pi.hProcess, 89);
        WaitForSingleObject(pi.hProcess, 1000);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        return 88;
    }
    last = GetLastError();
    /* Do not mistake arbitrary invalid paths/arguments for Job policy. */
    if (last != ERROR_ACCESS_DENIED && last != ERROR_NOT_ENOUGH_QUOTA)
        return 89;
    return 0;
}

/* Only 127.0.0.1 to a live manager-owned CI listener. No hostname or remote IP. */
static int probe_loopback(const wchar_t *port_str, const char **reply_result) {
    wchar_t *end = NULL;
    unsigned long number = wcstoul(port_str, &end, 10);
    WSADATA wsa;
    SOCKET sock = INVALID_SOCKET;
    struct sockaddr_in addr;
    int ret, error;
    int status = 93;
    if (!end || *end != L'\0' || number < 1 || number > 65535) return 90;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 91;
    sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) {
        status = (WSAGetLastError() == WSAEACCES) ? 0 : 92;
        if (status == 0) *reply_result = "PONG";
        goto done;
    }
    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short)number);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    {
        u_long nonblocking = 1;
        fd_set write_set, error_set;
        struct timeval deadline;
        int selected;
        int so_error = 0;
        int len = sizeof(so_error);
        if (ioctlsocket(sock, FIONBIO, &nonblocking) != 0) goto done;
        ret = connect(sock, (const struct sockaddr *)&addr, sizeof(addr));
        if (ret == 0) { status = 94; goto done; }  /* security failure */
        error = WSAGetLastError();
        if (error == WSAEACCES) {
            *reply_result = "PONG";  /* explicit WSAEACCES */
            status = 0; goto done;
        }
        if (error != WSAEWOULDBLOCK) goto done;
        FD_ZERO(&write_set);
        FD_ZERO(&error_set);
        FD_SET(sock, &write_set);
        FD_SET(sock, &error_set);
        deadline.tv_sec = 0;
        deadline.tv_usec = 500000;
        selected = select(0, NULL, &write_set, &error_set, &deadline);
        if (selected == 0) {
            *reply_result = "DENIED"; /* means INCONCLUSIVE TIMEOUT, NOT OS denial */
            status = 0; goto done;
        }
        if (selected < 0 || getsockopt(sock, SOL_SOCKET, SO_ERROR,
                                       (char *)&so_error, &len) != 0) goto done;
        if (so_error == WSAEACCES) {
            *reply_result = "PONG";
            status = 0; goto done;
        }
        if (so_error == 0) status = 94; /* TCP connection succeeded => fail */
    }
done:
    if (sock != INVALID_SOCKET) closesocket(sock);
    WSACleanup();
    return status;
}

int wmain(int argc, wchar_t **argv) {
    /* Developer-owned CI scenarios only. No worker-facing API or commands. */
    const wchar_t *mode = (argc == 1) ? L"ok" : ((argc == 2 || argc == 3) ? argv[1] : L"invalid");
    const char *result_token = "PONG";
    const char *result_digest = DIGEST;
    if (wcscmp(mode, L"ok") && wcscmp(mode, L"crash") &&
        wcscmp(mode, L"hang") && wcscmp(mode, L"truncated") &&
        wcscmp(mode, L"oversized") && wcscmp(mode, L"wrong_nonce") &&
        wcscmp(mode, L"trailing") && wcscmp(mode, L"handle") &&
        wcscmp(mode, L"breakaway") && wcscmp(mode, L"network")) return 42;
    if ((wcscmp(mode, L"handle") == 0 || wcscmp(mode, L"network") == 0)
          ? argc != 3 : argc > 2) return 43;
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
    if (wcscmp(mode, L"handle") == 0) {
        int status = deny_inherited_event(argv[2]);
        if (status != 0) return status;
    }
    if (wcscmp(mode, L"breakaway") == 0) {
        int status = deny_job_breakaway();
        if (status != 0) return status;
    }
    if (wcscmp(mode, L"network") == 0) {
        int status = probe_loopback(argv[2], &result_token);
        if (status != 0) return status;
        result_digest = (strcmp(result_token, "PONG") == 0) ? DIGEST :
            "0000000000000000000000000000000000000000000000000000000000000000";
    }
    if (wcscmp(mode, L"wrong_nonce") == 0)
        nonce[0] = (nonce[0] == 'a') ? 'b' : 'a';
    count = snprintf(reply, sizeof(reply),
        "{\"attempt_id\":\"ATT-%s\",\"capability\":\"PROBE_PING\","
        "\"job_id\":\"JOB-%s\",\"kind\":\"response\",\"nonce\":\"%s\","
        "\"observation_sha256\":\"%s\",\"operation_id\":\"OP-%s\","
        "\"result\":\"%s\",\"version\":1}",
        attempt, job, nonce, result_digest, operation, result_token);
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
