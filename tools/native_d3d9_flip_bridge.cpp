// D3D9兼容显示桥：保留系统D3D9及原参数，将最终回缓冲复制到同一游戏窗口的D3D11 Flip交换链。
// 已验证当前构建大厅交互；CPU回读开销、战斗及其他构建仍待验收，不读取其他进程或认证数据。
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d9.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <atomic>
#include <new>
#include <mutex>
#include <cstdio>
#include <cstring>

using Microsoft::WRL::ComPtr;
static HMODULE own_module = nullptr;

// 始终按系统目录绝对路径加载原库，避免再次加载本DLL形成递归；不在DllMain中初始化图形。
static HMODULE SystemD3D9() {
    static HMODULE module = []() {
        wchar_t directory[MAX_PATH]{};
        if (!GetSystemDirectoryW(directory, MAX_PATH)) return static_cast<HMODULE>(nullptr);
        wcscat_s(directory, L"\\d3d9.dll");
        return LoadLibraryExW(directory, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    }();
    return module;
}

// 只记录HRESULT和帧数；受控测试可指定私人证据路径，默认位于本DLL旁，不记录身份或像素。
static void Record(const char* stage, HRESULT result, unsigned frames) {
    wchar_t path[MAX_PATH]{};
    DWORD configured = GetEnvironmentVariableW(L"NZ_FRAME_BRIDGE_LOG", path, MAX_PATH);
    if (configured >= MAX_PATH) return;
    if (!configured) {
        if (!GetModuleFileNameW(own_module, path, MAX_PATH)) return;
        wchar_t* end = wcsrchr(path, L'\\');
        if (!end) return;
        wcscpy_s(end + 1, MAX_PATH - (end + 1 - path), L"nz-flip-bridge.log");
    }
    FILE* output = nullptr;
    if (_wfopen_s(&output, path, L"ab") == 0) {
        fprintf(output, "%s hr=%ld frames=%u\n", stage, result, frames);
        fclose(output);
    }
}

struct DeviceState {
    HWND window = nullptr;
    unsigned width = 0, height = 0, frames = 0;
    HRESULT last_error = S_OK;
    ComPtr<ID3D11Device> display;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDXGISwapChain1> swap;
    std::recursive_mutex frame_mutex;

    // 释放自有显示资源；不会持有D3D9资源，避免它们反向增加游戏设备引用并阻塞Reset/Release。
    void ClearDisplay() {
        swap.Reset(); context.Reset(); display.Reset();
        // HWND属于游戏；本桥不创建、销毁窗口，不替换窗口过程，也不接管游戏的消息循环。
        window = nullptr; width = height = 0;
    }
    ~DeviceState() { ClearDisplay(); }

    // 构建与源图像等大的Flip交换链，使用现有游戏HWND；不更改分辨率或全屏设置。
    HRESULT EnsureDisplay(HWND target, const D3DSURFACE_DESC& source) {
        if (swap && window == target && width == source.Width && height == source.Height) return S_OK;
        ClearDisplay();
        if (!IsWindow(target) || !source.Width || !source.Height || source.Width > 8192 || source.Height > 8192)
            return E_INVALIDARG;
        window = target; width = source.Width; height = source.Height;
        D3D_FEATURE_LEVEL level{};
        HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &display, &level, &context);
        ComPtr<IDXGIDevice> dxgi; ComPtr<IDXGIAdapter> adapter; ComPtr<IDXGIFactory2> factory;
        if (SUCCEEDED(hr)) hr = display.As(&dxgi);
        if (SUCCEEDED(hr)) hr = dxgi->GetAdapter(&adapter);
        if (SUCCEEDED(hr)) hr = adapter->GetParent(IID_PPV_ARGS(&factory));
        DXGI_SWAP_CHAIN_DESC1 desc{}; desc.Width = width; desc.Height = height;
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM; desc.SampleDesc.Count = 1;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; desc.BufferCount = 2;
        desc.Scaling = DXGI_SCALING_STRETCH; desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
        desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;
        if (SUCCEEDED(hr)) hr = factory->CreateSwapChainForHwnd(display.Get(), window, &desc, nullptr, nullptr, &swap);
        if (SUCCEEDED(hr)) factory->MakeWindowAssociation(window, DXGI_MWA_NO_ALT_ENTER);
        Record("display-created", hr, frames);
        if (FAILED(hr)) ClearDisplay();
        return hr;
    }

    // 只复制当前设备的回缓冲，支持常见32位格式和多重采样解析；异常时保留原始Present结果。
    HRESULT CopyFrame(IDirect3DDevice9* device, IDirect3DSurface9* back, HWND target) {
        D3DSURFACE_DESC desc{};
        HRESULT hr = back->GetDesc(&desc);
        if (FAILED(hr)) return hr;
        if (desc.Format != D3DFMT_X8R8G8B8 && desc.Format != D3DFMT_A8R8G8B8) return D3DERR_NOTAVAILABLE;
        ComPtr<IDirect3DSurface9> resolved, readback;
        IDirect3DSurface9* source = back;
        if (desc.MultiSampleType != D3DMULTISAMPLE_NONE) {
            hr = device->CreateRenderTarget(desc.Width, desc.Height, desc.Format, D3DMULTISAMPLE_NONE,
                0, FALSE, &resolved, nullptr);
            if (SUCCEEDED(hr)) hr = device->StretchRect(back, nullptr, resolved.Get(), nullptr, D3DTEXF_NONE);
            if (FAILED(hr)) return hr;
            source = resolved.Get();
        }
        hr = device->CreateOffscreenPlainSurface(desc.Width, desc.Height, desc.Format, D3DPOOL_SYSTEMMEM, &readback, nullptr);
        if (SUCCEEDED(hr)) hr = device->GetRenderTargetData(source, readback.Get());
        if (FAILED(hr)) return hr;
        hr = EnsureDisplay(target, desc);
        if (FAILED(hr)) return hr;
        ComPtr<ID3D11Texture2D> destination;
        hr = swap->GetBuffer(0, IID_PPV_ARGS(&destination));
        D3DLOCKED_RECT pixels{};
        if (SUCCEEDED(hr)) hr = readback->LockRect(&pixels, nullptr, D3DLOCK_READONLY);
        if (SUCCEEDED(hr)) {
            context->UpdateSubresource(destination.Get(), 0, nullptr, pixels.pBits, pixels.Pitch, 0);
            readback->UnlockRect();
        }
        return hr;
    }

    // 记录首帧、错误变化和每600帧进度；返回值只描述桥，绝不伪造游戏原始API的成功。
    void DisplayFrame(HRESULT copied) {
        HRESULT result = SUCCEEDED(copied) ? swap->Present(0, 0) : copied;
        frames++;
        if (frames == 1 || frames % 600 == 0 || result != last_error) Record("frame", result, frames);
        last_error = result;
    }
};

// 显式代理类具有独立C++对象；不写原始系统对象内存，不依赖其私有布局。
#include "native_d3d9_forwarders.inc"

// 在原始Present前读取源帧，原始Present后显示；返回真实原始结果，不改变传入参数。
HRESULT WINAPI DeviceProxy::Present(const RECT* source, const RECT* dest, HWND override_window, const RGNDATA* dirty) {
    std::lock_guard<std::recursive_mutex> lock(display.frame_mutex);
    ComPtr<IDirect3DSurface9> back;
    HRESULT copied = real->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &back);
    ComPtr<IDirect3DSwapChain9> chain;
    D3DPRESENT_PARAMETERS parameters{};
    if (SUCCEEDED(copied)) copied = real->GetSwapChain(0, &chain);
    if (SUCCEEDED(copied)) copied = chain->GetPresentParameters(&parameters);
    if (SUCCEEDED(copied)) copied = display.CopyFrame(real, back.Get(), override_window ? override_window : parameters.hDeviceWindow);
    back.Reset(); chain.Reset();
    HRESULT result = real->Present(source, dest, override_window, dirty);
    display.DisplayFrame(copied);
    return result;
}

// 交换链可能有独立窗口；只复制该链自己的回缓冲，并保留真实Present的所有参数和结果。
HRESULT WINAPI SwapProxy::Present(const RECT* source, const RECT* dest, HWND override_window, const RGNDATA* dirty, DWORD flags) {
    std::lock_guard<std::recursive_mutex> lock(display.frame_mutex);
    ComPtr<IDirect3DSurface9> back;
    D3DPRESENT_PARAMETERS parameters{};
    HRESULT copied = real->GetBackBuffer(0, D3DBACKBUFFER_TYPE_MONO, &back);
    if (SUCCEEDED(copied)) copied = real->GetPresentParameters(&parameters);
    if (SUCCEEDED(copied)) copied = display.CopyFrame(owner->real, back.Get(), override_window ? override_window : parameters.hDeviceWindow);
    back.Reset();
    HRESULT result = real->Present(source, dest, override_window, dirty, flags);
    display.DisplayFrame(copied);
    return result;
}

// 重置前释放本层的显示资源；没有缓存系统D3D9默认池资源。
HRESULT WINAPI DeviceProxy::Reset(D3DPRESENT_PARAMETERS* parameters) {
    std::lock_guard<std::recursive_mutex> lock(display.frame_mutex);
    display.ClearDisplay();
    return real->Reset(parameters);
}

// 工厂父引用返回同一个代理，防止调用方通过GetDirect3D误进入另一条未记录的创建路径。
HRESULT WINAPI DeviceProxy::GetDirect3D(IDirect3D9** output) {
    if (!output) return D3DERR_INVALIDCALL;
    *output = factory; factory->AddRef(); return D3D_OK;
}

// 交换链返回同一个设备代理，并正确增加公开引用。
HRESULT WINAPI SwapProxy::GetDevice(IDirect3DDevice9** output) {
    if (!output) return D3DERR_INVALIDCALL;
    *output = owner; owner->AddRef(); return D3D_OK;
}

// 接管刚取得的系统链引用；内存不足时归还它并明确报告失败。
static HRESULT WrapSwap(HRESULT result, IDirect3DSwapChain9** output, DeviceProxy* owner) {
    if (SUCCEEDED(result) && output && *output) {
        auto proxy = new (std::nothrow) SwapProxy(*output, owner);
        if (!proxy) { (*output)->Release(); *output = nullptr; return E_OUTOFMEMORY; }
        *output = proxy;
    }
    return result;
}

// 显式覆盖额外链创建，保留多窗口的系统参数校验。
HRESULT WINAPI DeviceProxy::CreateAdditionalSwapChain(D3DPRESENT_PARAMETERS* parameters, IDirect3DSwapChain9** output) {
    return WrapSwap(real->CreateAdditionalSwapChain(parameters, output), output, this);
}

// 获取已有链时每个代理接管一次系统引用，正常Release时归还。
HRESULT WINAPI DeviceProxy::GetSwapChain(UINT index, IDirect3DSwapChain9** output) {
    return WrapSwap(real->GetSwapChain(index, output), output, this);
}

// 原样创建D3D9设备；不启用9Ex，不修改能力、采样、分辨率或窗口模式。
HRESULT WINAPI FactoryProxy::CreateDevice(UINT adapter, D3DDEVTYPE type, HWND focus, DWORD flags,
    D3DPRESENT_PARAMETERS* parameters, IDirect3DDevice9** output) {
    HRESULT result = real->CreateDevice(adapter, type, focus, flags, parameters, output);
    Record("game-device-created", result, 0);
    if (SUCCEEDED(result) && output && *output) {
        auto proxy = new (std::nothrow) DeviceProxy(*output, this);
        if (!proxy) { (*output)->Release(); *output = nullptr; return E_OUTOFMEMORY; }
        *output = proxy;
    }
    return result;
}

// 导出标准工厂入口；只在系统成功返回真实工厂后增加本地显式代理。
extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk) {
    using Fn = IDirect3D9* (WINAPI*)(UINT);
    auto function = reinterpret_cast<Fn>(GetProcAddress(SystemD3D9(), "Direct3DCreate9"));
    IDirect3D9* factory = function ? function(sdk) : nullptr;
    if (!factory) return nullptr;
    auto proxy = new (std::nothrow) FactoryProxy(factory);
    if (!proxy) factory->Release();
    return proxy;
}

// Ex不是本实验覆盖范围，原样转交系统实现，绝不强制把D3D9升级为Ex。
extern "C" HRESULT WINAPI Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex** output) {
    using Fn = HRESULT (WINAPI*)(UINT, IDirect3D9Ex**);
    auto function = reinterpret_cast<Fn>(GetProcAddress(SystemD3D9(), "Direct3DCreate9Ex"));
    return function ? function(sdk, output) : D3DERR_NOTAVAILABLE;
}

// 性能标记仅转发到系统库；它们不读取或记录调用方的标记文本。
#define FORWARD(return_type, name, signature, arguments) \
extern "C" return_type WINAPI name signature { \
    using Fn = return_type (WINAPI*) signature; \
    auto function = reinterpret_cast<Fn>(GetProcAddress(SystemD3D9(), #name)); \
    return function arguments; }
FORWARD(int, D3DPERF_BeginEvent, (D3DCOLOR color, LPCWSTR name), (color, name))
FORWARD(int, D3DPERF_EndEvent, (void), ())
FORWARD(void, D3DPERF_SetMarker, (D3DCOLOR color, LPCWSTR name), (color, name))
FORWARD(void, D3DPERF_SetRegion, (D3DCOLOR color, LPCWSTR name), (color, name))
FORWARD(BOOL, D3DPERF_QueryRepeatFrame, (void), ())
FORWARD(void, D3DPERF_SetOptions, (DWORD options), (options))
FORWARD(DWORD, D3DPERF_GetStatus, (void), ())

// 加载阶段只保存模块句柄；无线程、文件、网络或图形初始化，避免Loader Lock问题。
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) { own_module = instance; DisableThreadLibraryCalls(instance); }
    return TRUE;
}
