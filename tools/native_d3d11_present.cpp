// 独立图形链路对照：顶部GDI蓝条、下方D3D11绿色区域，区分窗口绘制与GPU交换链。
// 只操作自身窗口及资源；不注入游戏、不更改显示/驱动配置、不截取其他窗口。
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <cstdio>
#include <cstdint>
#include <cwchar>

static unsigned paints = 0;

// GDI参考区域独立于子窗口交换链，每秒重绘，方便远程观察是否只有GPU区域失效。
static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    if (message == WM_PAINT) {
        PAINTSTRUCT paint{}; HDC dc = BeginPaint(window, &paint);
        RECT rect{0, 0, 640, 70}; HBRUSH blue = CreateSolidBrush(RGB(20, 60, 190));
        FillRect(dc, &rect, blue); DeleteObject(blue);
        SetBkMode(dc, TRANSPARENT); SetTextColor(dc, RGB(255, 255, 255));
        DrawTextW(dc, L"普通绘制：此条应为蓝色；下方应为绿色", -1, &rect,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        EndPaint(window, &paint); paints++; return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

// 参数为新证据文件及可选--warp/--flip/--observe；观察模式延长到十分钟，仍可正常关闭。
int wmain(int argc, wchar_t** argv) {
    if (argc < 2 || argc > 5 || GetFileAttributesW(argv[1]) != INVALID_FILE_ATTRIBUTES) return 2;
    bool warp = false, flip = false, observe = false;
    for (int index=2; index<argc; index++) {
        if (!wcscmp(argv[index], L"--warp") && !warp) warp = true;
        else if (!wcscmp(argv[index], L"--flip") && !flip) flip = true;
        else if (!wcscmp(argv[index], L"--observe") && !observe) observe = true;
        else return 2;
    }
    FILE* output = nullptr;
    if (_wfopen_s(&output, argv[1], L"wb")) return 3;
    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSW cls{}; cls.lpfnWndProc = WindowProc; cls.hInstance = instance;
    cls.lpszClassName = L"NZOwnedD3D11Diagnostic"; cls.hCursor = LoadCursor(nullptr, IDC_ARROW);
    if (!RegisterClassW(&cls)) { fclose(output); return 4; }
    RECT bounds{0, 0, 640, 430}; AdjustWindowRect(&bounds, WS_OVERLAPPEDWINDOW, FALSE);
    HWND window = CreateWindowExW(0, cls.lpszClassName,
        flip ? L"NZ D3D11 Flip 对照 · 自动关闭" :
        (warp ? L"NZ D3D11 软件对照 · 自动关闭" : L"NZ D3D11 硬件对照 · 自动关闭"),
        WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN, CW_USEDEFAULT, CW_USEDEFAULT,
        bounds.right-bounds.left, bounds.bottom-bounds.top, nullptr, nullptr, instance, nullptr);
    HWND surface = CreateWindowExW(0, L"STATIC", L"", WS_CHILD | WS_VISIBLE,
        0, 70, 640, 360, window, nullptr, instance, nullptr);
    if (window) { ShowWindow(window, SW_SHOWNORMAL); UpdateWindow(window); }
    DXGI_SWAP_CHAIN_DESC desc{}; desc.BufferCount = flip ? 2 : 1;
    desc.BufferDesc.Width = 640; desc.BufferDesc.Height = 360;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; desc.OutputWindow = surface;
    desc.SampleDesc.Count = 1; desc.Windowed = TRUE;
    desc.SwapEffect = flip ? DXGI_SWAP_EFFECT_FLIP_DISCARD : DXGI_SWAP_EFFECT_DISCARD;
    ID3D11Device* device = nullptr; ID3D11DeviceContext* context = nullptr;
    IDXGISwapChain* chain = nullptr; ID3D11Texture2D* back = nullptr;
    ID3D11Texture2D* staging = nullptr; ID3D11RenderTargetView* target = nullptr;
    D3D_FEATURE_LEVEL level{};
    HRESULT created = surface ? D3D11CreateDeviceAndSwapChain(nullptr,
        warp ? D3D_DRIVER_TYPE_WARP : D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
        D3D11_SDK_VERSION, &desc, &chain, &device, &level, &context) : E_FAIL;
    HRESULT target_hr = E_FAIL, read_hr = E_FAIL, present_hr = E_FAIL;
    bool pixel_match = false; unsigned frames = 0, failures = 0, present_ok = 0, other_status = 0;
    if (SUCCEEDED(created)) {
        target_hr = chain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&back));
        if (SUCCEEDED(target_hr)) target_hr = device->CreateRenderTargetView(back, nullptr, &target);
    }
    if (SUCCEEDED(target_hr)) {
        const ULONGLONG deadline = GetTickCount64()+(observe ? 600000 : 120000); MSG message{}; bool quit = false;
        const float green[]{34.0f/255, 170.0f/255, 119.0f/255, 1.0f};
        while (!quit && GetTickCount64() < deadline) {
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                if (message.message == WM_QUIT) { quit = true; break; }
                TranslateMessage(&message); DispatchMessageW(&message);
            }
            if (quit) break;
            context->ClearRenderTargetView(target, green);
            if (frames == 0) {
                // 在CPU侧回读自身GPU颜色，不能将此结果替代远程窗口实际可见结果。
                D3D11_TEXTURE2D_DESC read_desc{}; back->GetDesc(&read_desc);
                read_desc.Usage = D3D11_USAGE_STAGING; read_desc.BindFlags = 0;
                read_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ; read_desc.MiscFlags = 0;
                read_hr = device->CreateTexture2D(&read_desc, nullptr, &staging);
                if (SUCCEEDED(read_hr)) {
                    context->CopyResource(staging, back); D3D11_MAPPED_SUBRESOURCE mapped{};
                    read_hr = context->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
                    if (SUCCEEDED(read_hr)) {
                        pixel_match = (*static_cast<const uint32_t*>(mapped.pData) & 0x00ffffff) == 0x0077aa22;
                        context->Unmap(staging, 0);
                    }
                }
            }
            present_hr = chain->Present(0, 0);
            if (present_hr == S_OK) present_ok++; else other_status++;
            if (FAILED(present_hr)) failures++;
            if (!(frames % 60)) { RECT rect{0,0,640,70}; InvalidateRect(window, &rect, FALSE); }
            frames++; Sleep(16);
        }
    }
    fprintf(output, "{\"kind\":\"OWNED_D3D11_GDI_NOT_GAME\",\"backend\":\"%s\",\"flip\":%s,\"x86\":%s,\"create_hr\":%ld,\"target_hr\":%ld,\"readback_hr\":%ld,\"present_hr\":%ld,\"feature_level\":%u,\"pixel_matches\":%s,\"frames\":%u,\"failures\":%u,\"presented_ok\":%u,\"present_other_status\":%u,\"gdi_paints\":%u,\"game_modified\":false}",
        warp ? "d3d11-warp" : "d3d11-hardware", flip ? "true" : "false", sizeof(void*)==4 ? "true" : "false",
        created, target_hr, read_hr, present_hr, static_cast<unsigned>(level), pixel_match ? "true" : "false",
        frames, failures, present_ok, other_status, paints);
    fclose(output);
    if (staging) staging->Release(); if (target) target->Release(); if (back) back->Release();
    if (chain) chain->Release(); if (context) context->Release(); if (device) device->Release();
    if (window && IsWindow(window)) DestroyWindow(window);
    UnregisterClassW(cls.lpszClassName, instance);
    return FAILED(target_hr) || failures || !pixel_match || !present_ok || !paints ? 1 : 0;
}
