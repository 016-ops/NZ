// 独立x86 D3D9呈现诊断：创建自己的普通窗口及设备，核对回缓冲像素和Present返回值。
// 不注入游戏、不更改显示模式或驱动配置，不把本程序成功当作原生游戏绘制成功。
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <d3d9on12.h>
#include <cstdio>
#include <cstdint>
#include <cwchar>

// 仅处理诊断窗口生命周期；关闭时让主循环停止并释放自己的COM资源。
static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, wparam, lparam);
}

// 输入为新证据路径及可选--on12/--flipex；只调用系统公开API，不修改系统或游戏DLL。
// 明确选择On12时不静默退回D3D9，便于区分“未支持”和“支持但呈现失败”。
int wmain(int argc, wchar_t** argv) {
    if ((argc != 2 && argc != 3) || (argc == 3 && wcscmp(argv[2], L"--on12") && wcscmp(argv[2], L"--flipex")) ||
        GetFileAttributesW(argv[1]) != INVALID_FILE_ATTRIBUTES) return 2;
    const bool on12 = argc == 3 && !wcscmp(argv[2], L"--on12");
    const bool flipex = argc == 3 && !wcscmp(argv[2], L"--flipex");
    FILE* output = nullptr;
    if (_wfopen_s(&output, argv[1], L"wb") != 0) return 3;
    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSW cls{}; cls.lpfnWndProc = WindowProc; cls.hInstance = instance;
    cls.lpszClassName = L"NZOwnedD3D9Diagnostic"; cls.hCursor = LoadCursor(nullptr, IDC_ARROW);
    if (!RegisterClassW(&cls)) { fclose(output); return 4; }
    HWND window = CreateWindowExW(0, cls.lpszClassName,
        flipex ? L"NZ D3D9Ex Flip 诊断 · 自动关闭" :
        (on12 ? L"NZ D3D9On12 诊断 · 自动关闭" : L"NZ D3D9 诊断 · 自动关闭"),
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 660, 400, nullptr, nullptr, instance, nullptr);
    // 强制System32搜索规则；x86进程由Windows重定向至对应的系统32位库。
    HMODULE system_d3d = LoadLibraryExW(L"d3d9.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    auto create_on12 = system_d3d ? reinterpret_cast<PFN_Direct3DCreate9On12>(
        GetProcAddress(system_d3d, "Direct3DCreate9On12")) : nullptr;
    D3D9ON12_ARGS override_args{}; override_args.Enable9On12 = TRUE;
    IDirect3D9Ex* d3d_ex = nullptr;
    if (flipex) Direct3DCreate9Ex(D3D_SDK_VERSION, &d3d_ex);
    IDirect3D9* d3d = flipex ? d3d_ex : (on12 ? (create_on12 ? create_on12(D3D_SDK_VERSION, &override_args, 1) : nullptr)
        : Direct3DCreate9(D3D_SDK_VERSION));
    IDirect3DDevice9* device = nullptr;
    IDirect3DDevice9Ex* device_ex = nullptr;
    D3DPRESENT_PARAMETERS pp{}; pp.Windowed = TRUE; pp.hDeviceWindow = window;
    pp.SwapEffect = flipex ? D3DSWAPEFFECT_FLIPEX : D3DSWAPEFFECT_DISCARD; pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferWidth = 640; pp.BackBufferHeight = 360; pp.BackBufferCount = flipex ? 2 : 1;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    HRESULT created = E_FAIL, cleared = E_FAIL, presented = E_FAIL, copied = E_FAIL;
    HRESULT on12_interface = E_NOINTERFACE;
    bool pixel_match = false; unsigned frames = 0, failures = 0, presented_ok = 0, present_other_status = 0;
    if (window && d3d) {
        ShowWindow(window, SW_SHOWNORMAL);
        if (flipex) {
            created = d3d_ex->CreateDeviceEx(0, D3DDEVTYPE_HAL, window,
                D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, nullptr, &device_ex);
            device = device_ex;
        } else created = d3d->CreateDevice(0, D3DDEVTYPE_HAL, window,
            D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &device);
    }
    if (SUCCEEDED(created)) {
        // 此接口只会由D3D9On12设备提供，用真实设备查询验证选择的后端。
        IDirect3DDevice9On12* translated_device = nullptr;
        on12_interface = device->QueryInterface(__uuidof(IDirect3DDevice9On12),
            reinterpret_cast<void**>(&translated_device));
        if (translated_device) translated_device->Release();
        const ULONGLONG deadline = GetTickCount64() + 120000;
        MSG message{}; bool quit = false;
        while (!quit && GetTickCount64() < deadline) {
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                if (message.message == WM_QUIT) { quit = true; break; }
                TranslateMessage(&message); DispatchMessageW(&message);
            }
            if (quit) break;
            cleared = device->Clear(0, nullptr, D3DCLEAR_TARGET, 0x0022aa77, 1.0f, 0);
            if (frames == 0 && SUCCEEDED(cleared)) {
                // 仅回读自己的回缓冲，不读取屏幕其他窗口或用户内容。
                IDirect3DSurface9 *back = nullptr, *readback = nullptr;
                copied = device->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &back);
                if (SUCCEEDED(copied)) copied = device->CreateOffscreenPlainSurface(640, 360,
                    pp.BackBufferFormat, D3DPOOL_SYSTEMMEM, &readback, nullptr);
                if (SUCCEEDED(copied)) copied = device->GetRenderTargetData(back, readback);
                D3DLOCKED_RECT lock{};
                if (SUCCEEDED(copied)) {
                    copied = readback->LockRect(&lock, nullptr, D3DLOCK_READONLY);
                    if (SUCCEEDED(copied)) {
                        pixel_match = (*static_cast<const uint32_t*>(lock.pBits) & 0x00ffffff) == 0x0022aa77;
                        readback->UnlockRect();
                    }
                }
                if (readback) readback->Release();
                if (back) back->Release();
            }
            presented = device_ex ? device_ex->PresentEx(nullptr, nullptr, nullptr, nullptr, 0)
                : device->Present(nullptr, nullptr, nullptr, nullptr);
            // S_PRESENT_OCCLUDED不是失败HRESULT，但也不能当作实际呈现；单独统计所有非零状态。
            if (presented == D3D_OK) presented_ok++; else present_other_status++;
            if (FAILED(cleared) || FAILED(presented)) failures++;
            frames++; Sleep(16);
        }
    }
    fprintf(output, "{\"kind\":\"OWNED_X86_D3D9_PRESENT_NOT_GAME\",\"backend\":\"%s\",\"on12_export_available\":%s,\"on12_interface_hr\":%ld,\"x86\":%s,\"create_hr\":%ld,\"clear_hr\":%ld,\"present_hr\":%ld,\"readback_hr\":%ld,\"pixel_matches\":%s,\"frames\":%u,\"failures\":%u,\"presented_ok\":%u,\"present_other_status\":%u,\"game_modified\":false}",
        flipex ? "system-d3d9ex-flipex" : (on12 ? "system-d3d9on12" : "system-d3d9"), create_on12 ? "true" : "false", on12_interface,
        sizeof(void*) == 4 ? "true" : "false", created, cleared, presented, copied, pixel_match ? "true" : "false", frames, failures, presented_ok, present_other_status);
    fclose(output);
    if (device) device->Release();
    if (d3d) d3d->Release();
    if (system_d3d) FreeLibrary(system_d3d);
    if (window && IsWindow(window)) DestroyWindow(window);
    UnregisterClassW(cls.lpszClassName, instance);
    return FAILED(created) || failures || !pixel_match || !presented_ok || (on12 && FAILED(on12_interface)) ? 1 : 0;
}
