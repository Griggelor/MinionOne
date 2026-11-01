/*
 * v0.37 fixed synthetic Windows AppContainer denial-test helper.
 * NOT a worker tool, general interpreter, script runner or release executable.
 * Build in Windows CI using the trusted Visual C++ compiler.
 *
 * Args from the trusted test harness ONLY:
 * manager-canary, sibling-canary, immutable-output-canary, loopback-port
 * Return 0x40 | SUCCESS_MASK:
 *   1 manager READ succeeded, 2 sibling READ succeeded,
 *   4 output WRITE handle succeeded (never writes), 8 TCP connect succeeded.
 * Invalid test conditions return 0xE1..0xE9 and NEVER count as denial.
 * Expected: normal-process positive control 0x4f; AppContainer 0x40.
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>
#pragma comment(lib, "ws2_32.lib")

static int file_probe(const wchar_t *path, DWORD access, int *success) {
    HANDLE h = CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE |
                           FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        CloseHandle(h);
        *success = 1;
        return 0;
    }
    *success = 0;
    if (GetLastError() != ERROR_ACCESS_DENIED &&
        GetLastError() != ERROR_PRIVILEGE_NOT_HELD) {
        return -1; /* Never mistake missing file / invalid path for isolation */
    }
    return 0;
}

int wmain(int argc, wchar_t **argv) {
    int mask = 0, success = 0;
    unsigned long port = 0;
    wchar_t *end = NULL;
    WSADATA wsa;
    SOCKET sock = INVALID_SOCKET;
    struct sockaddr_in address;
    int result, code;

    /* Fixed bootstrap marker: prove the package can load and run the EXE. */
    if (argc == 1) return 0x51;
    if (argc != 5 || argv[1][0] == L'\0' || argv[2][0] == L'\0' ||
        argv[3][0] == L'\0' || argv[4][0] == L'\0') return 0xE1;
    port = wcstoul(argv[4], &end, 10);
    if (*end != L'\0' || port == 0 || port > 65535) return 0xE2;

    if (file_probe(argv[1], GENERIC_READ, &success) != 0) return 0xE3;
    if (success) mask |= 1;
    if (file_probe(argv[2], GENERIC_READ, &success) != 0) return 0xE4;
    if (success) mask |= 2;
    /* Obtain a write-capable handle ONLY; never mutate a file. */
    if (file_probe(argv[3], FILE_WRITE_DATA, &success) != 0) return 0xE5;
    if (success) mask |= 4;

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 0xE6;
    sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) {
        code = WSAGetLastError();
        WSACleanup();
        return code == WSAEACCES ? 0x40 | mask : 0xE7;
    }
    ZeroMemory(&address, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((u_short)port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    {
        u_long nonblocking = 1;
        fd_set writable, exceptional;
        struct timeval deadline;
        int selected;
        if (ioctlsocket(sock, FIONBIO, &nonblocking) != 0) {
            closesocket(sock);
            WSACleanup();
            return 0xE7;
        }
        result = connect(sock, (const struct sockaddr *)&address,
                         sizeof(address));
        if (result == 0) mask |= 8;
        else {
            code = WSAGetLastError();
            if (code == WSAEACCES) {
                /* Explicit OS/network-denial status. */
            } else if (code == WSAEWOULDBLOCK) {
                FD_ZERO(&writable);
                FD_ZERO(&exceptional);
                FD_SET(sock, &writable);
                FD_SET(sock, &exceptional);
                deadline.tv_sec = 1;
                deadline.tv_usec = 0;
                selected = select(0, NULL, &writable, &exceptional, &deadline);
                if (selected == 0) {
                    closesocket(sock);
                    WSACleanup();
                    return 0x90 | mask; /* Timeout observed; NOT access-denied proof. */
                }
                if (selected < 0) {
                    closesocket(sock);
                    WSACleanup();
                    return 0xE8;
                }
                {
                    int so_error = 0;
                    int length = sizeof(so_error);
                    if (getsockopt(sock, SOL_SOCKET, SO_ERROR,
                                   (char *)&so_error, &length) != 0) {
                        closesocket(sock);
                        WSACleanup();
                        return 0xE8;
                    }
                    if (so_error == 0) mask |= 8;
                    else if (so_error != WSAEACCES) {
                        closesocket(sock);
                        WSACleanup();
                        return 0xE8;
                    }
                }
            } else {
                closesocket(sock);
                WSACleanup();
                return 0xE8; /* Refused/invalid route != explicit denial. */
            }
        }
    }
    closesocket(sock);
    WSACleanup();
    return 0x40 | mask;
}
