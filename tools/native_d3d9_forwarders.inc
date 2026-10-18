// 本文件由generate_d3d9_forwarders.py生成；方法签名来自Windows SDK公开接口。
// 普通方法不改变参数/返回值。资源接口保持系统对象；本实验的覆盖边界另记于交接文档。
// 显式IDirect3D9代理；自身引用计数与所持系统对象引用分离，释放时只归还拥有的引用。
class FactoryProxy final : public IDirect3D9 {
public:
    IDirect3D9* real; std::atomic<ULONG> references{1};
    // 构造接管一个系统引用；析构归还本层拥有的对象和父接口。
    explicit FactoryProxy(IDirect3D9* value) : real(value) {}
    ~FactoryProxy() { real->Release(); }
    // 保持本层IUnknown身份；其他扩展接口沿用系统查询结果，不宣称已覆盖其呈现路径。
    HRESULT WINAPI QueryInterface(REFIID riid, void** ppvObj) override { if (!ppvObj) return E_POINTER; if (riid == __uuidof(IUnknown) || riid == __uuidof(IDirect3D9)) { *ppvObj = this; AddRef(); return S_OK; } return real->QueryInterface(riid, ppvObj); }
    // 线程安全地增加本代理引用，不干预系统对象内部资源引用。
    ULONG WINAPI AddRef() override { return ++references; }
    // 最后一个本层引用释放时销毁代理，返回剩余引用数。
    ULONG WINAPI Release() override { ULONG left = --references; if (!left) delete this; return left; }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI RegisterSoftwareDevice(void* pInitializeFunction) override { return real->RegisterSoftwareDevice(pInitializeFunction); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    UINT WINAPI GetAdapterCount() override { return real->GetAdapterCount(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetAdapterIdentifier(UINT Adapter,DWORD Flags,D3DADAPTER_IDENTIFIER9* pIdentifier) override { return real->GetAdapterIdentifier(Adapter, Flags, pIdentifier); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    UINT WINAPI GetAdapterModeCount(UINT Adapter,D3DFORMAT Format) override { return real->GetAdapterModeCount(Adapter, Format); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI EnumAdapterModes(UINT Adapter,D3DFORMAT Format,UINT Mode,D3DDISPLAYMODE* pMode) override { return real->EnumAdapterModes(Adapter, Format, Mode, pMode); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetAdapterDisplayMode(UINT Adapter,D3DDISPLAYMODE* pMode) override { return real->GetAdapterDisplayMode(Adapter, pMode); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CheckDeviceType(UINT Adapter,D3DDEVTYPE DevType,D3DFORMAT AdapterFormat,D3DFORMAT BackBufferFormat,BOOL bWindowed) override { return real->CheckDeviceType(Adapter, DevType, AdapterFormat, BackBufferFormat, bWindowed); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CheckDeviceFormat(UINT Adapter,D3DDEVTYPE DeviceType,D3DFORMAT AdapterFormat,DWORD Usage,D3DRESOURCETYPE RType,D3DFORMAT CheckFormat) override { return real->CheckDeviceFormat(Adapter, DeviceType, AdapterFormat, Usage, RType, CheckFormat); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CheckDeviceMultiSampleType(UINT Adapter,D3DDEVTYPE DeviceType,D3DFORMAT SurfaceFormat,BOOL Windowed,D3DMULTISAMPLE_TYPE MultiSampleType,DWORD* pQualityLevels) override { return real->CheckDeviceMultiSampleType(Adapter, DeviceType, SurfaceFormat, Windowed, MultiSampleType, pQualityLevels); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CheckDepthStencilMatch(UINT Adapter,D3DDEVTYPE DeviceType,D3DFORMAT AdapterFormat,D3DFORMAT RenderTargetFormat,D3DFORMAT DepthStencilFormat) override { return real->CheckDepthStencilMatch(Adapter, DeviceType, AdapterFormat, RenderTargetFormat, DepthStencilFormat); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CheckDeviceFormatConversion(UINT Adapter,D3DDEVTYPE DeviceType,D3DFORMAT SourceFormat,D3DFORMAT TargetFormat) override { return real->CheckDeviceFormatConversion(Adapter, DeviceType, SourceFormat, TargetFormat); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetDeviceCaps(UINT Adapter,D3DDEVTYPE DeviceType,D3DCAPS9* pCaps) override { return real->GetDeviceCaps(Adapter, DeviceType, pCaps); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HMONITOR WINAPI GetAdapterMonitor(UINT Adapter) override { return real->GetAdapterMonitor(Adapter); }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI CreateDevice(UINT Adapter,D3DDEVTYPE DeviceType,HWND hFocusWindow,DWORD BehaviorFlags,D3DPRESENT_PARAMETERS* pPresentationParameters,IDirect3DDevice9** ppReturnedDeviceInterface) override;
};
// 显式IDirect3DDevice9代理；自身引用计数与所持系统对象引用分离，释放时只归还拥有的引用。
class DeviceProxy final : public IDirect3DDevice9 {
public:
    IDirect3DDevice9* real; std::atomic<ULONG> references{1};
    // 构造接管一个系统引用；析构归还本层拥有的对象和父接口。
    FactoryProxy* factory; DeviceState display;
    DeviceProxy(IDirect3DDevice9* value, FactoryProxy* parent) : real(value), factory(parent) { factory->AddRef(); }
    ~DeviceProxy() { display.ClearDisplay(); real->Release(); factory->Release(); }
    // 保持本层IUnknown身份；其他扩展接口沿用系统查询结果，不宣称已覆盖其呈现路径。
    HRESULT WINAPI QueryInterface(REFIID riid, void** ppvObj) override { if (!ppvObj) return E_POINTER; if (riid == __uuidof(IUnknown) || riid == __uuidof(IDirect3DDevice9)) { *ppvObj = this; AddRef(); return S_OK; } return real->QueryInterface(riid, ppvObj); }
    // 线程安全地增加本代理引用，不干预系统对象内部资源引用。
    ULONG WINAPI AddRef() override { return ++references; }
    // 最后一个本层引用释放时销毁代理，返回剩余引用数。
    ULONG WINAPI Release() override { ULONG left = --references; if (!left) delete this; return left; }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI TestCooperativeLevel() override { return real->TestCooperativeLevel(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    UINT WINAPI GetAvailableTextureMem() override { return real->GetAvailableTextureMem(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI EvictManagedResources() override { return real->EvictManagedResources(); }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI GetDirect3D(IDirect3D9** ppD3D9) override;
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetDeviceCaps(D3DCAPS9* pCaps) override { return real->GetDeviceCaps(pCaps); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetDisplayMode(UINT iSwapChain,D3DDISPLAYMODE* pMode) override { return real->GetDisplayMode(iSwapChain, pMode); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetCreationParameters(D3DDEVICE_CREATION_PARAMETERS *pParameters) override { return real->GetCreationParameters(pParameters); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetCursorProperties(UINT XHotSpot,UINT YHotSpot,IDirect3DSurface9* pCursorBitmap) override { return real->SetCursorProperties(XHotSpot, YHotSpot, pCursorBitmap); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    void WINAPI SetCursorPosition(int X,int Y,DWORD Flags) override { return real->SetCursorPosition(X, Y, Flags); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    BOOL WINAPI ShowCursor(BOOL bShow) override { return real->ShowCursor(bShow); }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI CreateAdditionalSwapChain(D3DPRESENT_PARAMETERS* pPresentationParameters,IDirect3DSwapChain9** pSwapChain) override;
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI GetSwapChain(UINT iSwapChain,IDirect3DSwapChain9** pSwapChain) override;
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    UINT WINAPI GetNumberOfSwapChains() override { return real->GetNumberOfSwapChains(); }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI Reset(D3DPRESENT_PARAMETERS* pPresentationParameters) override;
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI Present(CONST RECT* pSourceRect,CONST RECT* pDestRect,HWND hDestWindowOverride,CONST RGNDATA* pDirtyRegion) override;
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetBackBuffer(UINT iSwapChain,UINT iBackBuffer,D3DBACKBUFFER_TYPE Type,IDirect3DSurface9** ppBackBuffer) override { return real->GetBackBuffer(iSwapChain, iBackBuffer, Type, ppBackBuffer); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetRasterStatus(UINT iSwapChain,D3DRASTER_STATUS* pRasterStatus) override { return real->GetRasterStatus(iSwapChain, pRasterStatus); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetDialogBoxMode(BOOL bEnableDialogs) override { return real->SetDialogBoxMode(bEnableDialogs); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    void WINAPI SetGammaRamp(UINT iSwapChain,DWORD Flags,CONST D3DGAMMARAMP* pRamp) override { return real->SetGammaRamp(iSwapChain, Flags, pRamp); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    void WINAPI GetGammaRamp(UINT iSwapChain,D3DGAMMARAMP* pRamp) override { return real->GetGammaRamp(iSwapChain, pRamp); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateTexture(UINT Width,UINT Height,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DTexture9** ppTexture,HANDLE* pSharedHandle) override { return real->CreateTexture(Width, Height, Levels, Usage, Format, Pool, ppTexture, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateVolumeTexture(UINT Width,UINT Height,UINT Depth,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DVolumeTexture9** ppVolumeTexture,HANDLE* pSharedHandle) override { return real->CreateVolumeTexture(Width, Height, Depth, Levels, Usage, Format, Pool, ppVolumeTexture, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateCubeTexture(UINT EdgeLength,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DCubeTexture9** ppCubeTexture,HANDLE* pSharedHandle) override { return real->CreateCubeTexture(EdgeLength, Levels, Usage, Format, Pool, ppCubeTexture, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateVertexBuffer(UINT Length,DWORD Usage,DWORD FVF,D3DPOOL Pool,IDirect3DVertexBuffer9** ppVertexBuffer,HANDLE* pSharedHandle) override { return real->CreateVertexBuffer(Length, Usage, FVF, Pool, ppVertexBuffer, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateIndexBuffer(UINT Length,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DIndexBuffer9** ppIndexBuffer,HANDLE* pSharedHandle) override { return real->CreateIndexBuffer(Length, Usage, Format, Pool, ppIndexBuffer, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateRenderTarget(UINT Width,UINT Height,D3DFORMAT Format,D3DMULTISAMPLE_TYPE MultiSample,DWORD MultisampleQuality,BOOL Lockable,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override { return real->CreateRenderTarget(Width, Height, Format, MultiSample, MultisampleQuality, Lockable, ppSurface, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateDepthStencilSurface(UINT Width,UINT Height,D3DFORMAT Format,D3DMULTISAMPLE_TYPE MultiSample,DWORD MultisampleQuality,BOOL Discard,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override { return real->CreateDepthStencilSurface(Width, Height, Format, MultiSample, MultisampleQuality, Discard, ppSurface, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI UpdateSurface(IDirect3DSurface9* pSourceSurface,CONST RECT* pSourceRect,IDirect3DSurface9* pDestinationSurface,CONST POINT* pDestPoint) override { return real->UpdateSurface(pSourceSurface, pSourceRect, pDestinationSurface, pDestPoint); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI UpdateTexture(IDirect3DBaseTexture9* pSourceTexture,IDirect3DBaseTexture9* pDestinationTexture) override { return real->UpdateTexture(pSourceTexture, pDestinationTexture); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetRenderTargetData(IDirect3DSurface9* pRenderTarget,IDirect3DSurface9* pDestSurface) override { return real->GetRenderTargetData(pRenderTarget, pDestSurface); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetFrontBufferData(UINT iSwapChain,IDirect3DSurface9* pDestSurface) override { return real->GetFrontBufferData(iSwapChain, pDestSurface); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI StretchRect(IDirect3DSurface9* pSourceSurface,CONST RECT* pSourceRect,IDirect3DSurface9* pDestSurface,CONST RECT* pDestRect,D3DTEXTUREFILTERTYPE Filter) override { return real->StretchRect(pSourceSurface, pSourceRect, pDestSurface, pDestRect, Filter); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI ColorFill(IDirect3DSurface9* pSurface,CONST RECT* pRect,D3DCOLOR color) override { return real->ColorFill(pSurface, pRect, color); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateOffscreenPlainSurface(UINT Width,UINT Height,D3DFORMAT Format,D3DPOOL Pool,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override { return real->CreateOffscreenPlainSurface(Width, Height, Format, Pool, ppSurface, pSharedHandle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetRenderTarget(DWORD RenderTargetIndex,IDirect3DSurface9* pRenderTarget) override { return real->SetRenderTarget(RenderTargetIndex, pRenderTarget); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetRenderTarget(DWORD RenderTargetIndex,IDirect3DSurface9** ppRenderTarget) override { return real->GetRenderTarget(RenderTargetIndex, ppRenderTarget); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetDepthStencilSurface(IDirect3DSurface9* pNewZStencil) override { return real->SetDepthStencilSurface(pNewZStencil); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetDepthStencilSurface(IDirect3DSurface9** ppZStencilSurface) override { return real->GetDepthStencilSurface(ppZStencilSurface); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI BeginScene() override { return real->BeginScene(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI EndScene() override { return real->EndScene(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI Clear(DWORD Count,CONST D3DRECT* pRects,DWORD Flags,D3DCOLOR Color,float Z,DWORD Stencil) override { return real->Clear(Count, pRects, Flags, Color, Z, Stencil); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetTransform(D3DTRANSFORMSTATETYPE State,CONST D3DMATRIX* pMatrix) override { return real->SetTransform(State, pMatrix); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetTransform(D3DTRANSFORMSTATETYPE State,D3DMATRIX* pMatrix) override { return real->GetTransform(State, pMatrix); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI MultiplyTransform(D3DTRANSFORMSTATETYPE argument0,CONST D3DMATRIX* argument1) override { return real->MultiplyTransform(argument0, argument1); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetViewport(CONST D3DVIEWPORT9* pViewport) override { return real->SetViewport(pViewport); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetViewport(D3DVIEWPORT9* pViewport) override { return real->GetViewport(pViewport); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetMaterial(CONST D3DMATERIAL9* pMaterial) override { return real->SetMaterial(pMaterial); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetMaterial(D3DMATERIAL9* pMaterial) override { return real->GetMaterial(pMaterial); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetLight(DWORD Index,CONST D3DLIGHT9* argument1) override { return real->SetLight(Index, argument1); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetLight(DWORD Index,D3DLIGHT9* argument1) override { return real->GetLight(Index, argument1); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI LightEnable(DWORD Index,BOOL Enable) override { return real->LightEnable(Index, Enable); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetLightEnable(DWORD Index,BOOL* pEnable) override { return real->GetLightEnable(Index, pEnable); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetClipPlane(DWORD Index,CONST float* pPlane) override { return real->SetClipPlane(Index, pPlane); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetClipPlane(DWORD Index,float* pPlane) override { return real->GetClipPlane(Index, pPlane); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetRenderState(D3DRENDERSTATETYPE State,DWORD Value) override { return real->SetRenderState(State, Value); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetRenderState(D3DRENDERSTATETYPE State,DWORD* pValue) override { return real->GetRenderState(State, pValue); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateStateBlock(D3DSTATEBLOCKTYPE Type,IDirect3DStateBlock9** ppSB) override { return real->CreateStateBlock(Type, ppSB); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI BeginStateBlock() override { return real->BeginStateBlock(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI EndStateBlock(IDirect3DStateBlock9** ppSB) override { return real->EndStateBlock(ppSB); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetClipStatus(CONST D3DCLIPSTATUS9* pClipStatus) override { return real->SetClipStatus(pClipStatus); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetClipStatus(D3DCLIPSTATUS9* pClipStatus) override { return real->GetClipStatus(pClipStatus); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetTexture(DWORD Stage,IDirect3DBaseTexture9** ppTexture) override { return real->GetTexture(Stage, ppTexture); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetTexture(DWORD Stage,IDirect3DBaseTexture9* pTexture) override { return real->SetTexture(Stage, pTexture); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetTextureStageState(DWORD Stage,D3DTEXTURESTAGESTATETYPE Type,DWORD* pValue) override { return real->GetTextureStageState(Stage, Type, pValue); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetTextureStageState(DWORD Stage,D3DTEXTURESTAGESTATETYPE Type,DWORD Value) override { return real->SetTextureStageState(Stage, Type, Value); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetSamplerState(DWORD Sampler,D3DSAMPLERSTATETYPE Type,DWORD* pValue) override { return real->GetSamplerState(Sampler, Type, pValue); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetSamplerState(DWORD Sampler,D3DSAMPLERSTATETYPE Type,DWORD Value) override { return real->SetSamplerState(Sampler, Type, Value); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI ValidateDevice(DWORD* pNumPasses) override { return real->ValidateDevice(pNumPasses); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetPaletteEntries(UINT PaletteNumber,CONST PALETTEENTRY* pEntries) override { return real->SetPaletteEntries(PaletteNumber, pEntries); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPaletteEntries(UINT PaletteNumber,PALETTEENTRY* pEntries) override { return real->GetPaletteEntries(PaletteNumber, pEntries); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetCurrentTexturePalette(UINT PaletteNumber) override { return real->SetCurrentTexturePalette(PaletteNumber); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetCurrentTexturePalette(UINT *PaletteNumber) override { return real->GetCurrentTexturePalette(PaletteNumber); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetScissorRect(CONST RECT* pRect) override { return real->SetScissorRect(pRect); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetScissorRect(RECT* pRect) override { return real->GetScissorRect(pRect); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetSoftwareVertexProcessing(BOOL bSoftware) override { return real->SetSoftwareVertexProcessing(bSoftware); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    BOOL WINAPI GetSoftwareVertexProcessing() override { return real->GetSoftwareVertexProcessing(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetNPatchMode(float nSegments) override { return real->SetNPatchMode(nSegments); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    float WINAPI GetNPatchMode() override { return real->GetNPatchMode(); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawPrimitive(D3DPRIMITIVETYPE PrimitiveType,UINT StartVertex,UINT PrimitiveCount) override { return real->DrawPrimitive(PrimitiveType, StartVertex, PrimitiveCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawIndexedPrimitive(D3DPRIMITIVETYPE argument0,INT BaseVertexIndex,UINT MinVertexIndex,UINT NumVertices,UINT startIndex,UINT primCount) override { return real->DrawIndexedPrimitive(argument0, BaseVertexIndex, MinVertexIndex, NumVertices, startIndex, primCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawPrimitiveUP(D3DPRIMITIVETYPE PrimitiveType,UINT PrimitiveCount,CONST void* pVertexStreamZeroData,UINT VertexStreamZeroStride) override { return real->DrawPrimitiveUP(PrimitiveType, PrimitiveCount, pVertexStreamZeroData, VertexStreamZeroStride); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawIndexedPrimitiveUP(D3DPRIMITIVETYPE PrimitiveType,UINT MinVertexIndex,UINT NumVertices,UINT PrimitiveCount,CONST void* pIndexData,D3DFORMAT IndexDataFormat,CONST void* pVertexStreamZeroData,UINT VertexStreamZeroStride) override { return real->DrawIndexedPrimitiveUP(PrimitiveType, MinVertexIndex, NumVertices, PrimitiveCount, pIndexData, IndexDataFormat, pVertexStreamZeroData, VertexStreamZeroStride); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI ProcessVertices(UINT SrcStartIndex,UINT DestIndex,UINT VertexCount,IDirect3DVertexBuffer9* pDestBuffer,IDirect3DVertexDeclaration9* pVertexDecl,DWORD Flags) override { return real->ProcessVertices(SrcStartIndex, DestIndex, VertexCount, pDestBuffer, pVertexDecl, Flags); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateVertexDeclaration(CONST D3DVERTEXELEMENT9* pVertexElements,IDirect3DVertexDeclaration9** ppDecl) override { return real->CreateVertexDeclaration(pVertexElements, ppDecl); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetVertexDeclaration(IDirect3DVertexDeclaration9* pDecl) override { return real->SetVertexDeclaration(pDecl); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetVertexDeclaration(IDirect3DVertexDeclaration9** ppDecl) override { return real->GetVertexDeclaration(ppDecl); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetFVF(DWORD FVF) override { return real->SetFVF(FVF); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetFVF(DWORD* pFVF) override { return real->GetFVF(pFVF); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateVertexShader(CONST DWORD* pFunction,IDirect3DVertexShader9** ppShader) override { return real->CreateVertexShader(pFunction, ppShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetVertexShader(IDirect3DVertexShader9* pShader) override { return real->SetVertexShader(pShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetVertexShader(IDirect3DVertexShader9** ppShader) override { return real->GetVertexShader(ppShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetVertexShaderConstantF(UINT StartRegister,CONST float* pConstantData,UINT Vector4fCount) override { return real->SetVertexShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetVertexShaderConstantF(UINT StartRegister,float* pConstantData,UINT Vector4fCount) override { return real->GetVertexShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetVertexShaderConstantI(UINT StartRegister,CONST int* pConstantData,UINT Vector4iCount) override { return real->SetVertexShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetVertexShaderConstantI(UINT StartRegister,int* pConstantData,UINT Vector4iCount) override { return real->GetVertexShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetVertexShaderConstantB(UINT StartRegister,CONST BOOL* pConstantData,UINT BoolCount) override { return real->SetVertexShaderConstantB(StartRegister, pConstantData, BoolCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetVertexShaderConstantB(UINT StartRegister,BOOL* pConstantData,UINT BoolCount) override { return real->GetVertexShaderConstantB(StartRegister, pConstantData, BoolCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetStreamSource(UINT StreamNumber,IDirect3DVertexBuffer9* pStreamData,UINT OffsetInBytes,UINT Stride) override { return real->SetStreamSource(StreamNumber, pStreamData, OffsetInBytes, Stride); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetStreamSource(UINT StreamNumber,IDirect3DVertexBuffer9** ppStreamData,UINT* pOffsetInBytes,UINT* pStride) override { return real->GetStreamSource(StreamNumber, ppStreamData, pOffsetInBytes, pStride); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetStreamSourceFreq(UINT StreamNumber,UINT Setting) override { return real->SetStreamSourceFreq(StreamNumber, Setting); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetStreamSourceFreq(UINT StreamNumber,UINT* pSetting) override { return real->GetStreamSourceFreq(StreamNumber, pSetting); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetIndices(IDirect3DIndexBuffer9* pIndexData) override { return real->SetIndices(pIndexData); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetIndices(IDirect3DIndexBuffer9** ppIndexData) override { return real->GetIndices(ppIndexData); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreatePixelShader(CONST DWORD* pFunction,IDirect3DPixelShader9** ppShader) override { return real->CreatePixelShader(pFunction, ppShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetPixelShader(IDirect3DPixelShader9* pShader) override { return real->SetPixelShader(pShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPixelShader(IDirect3DPixelShader9** ppShader) override { return real->GetPixelShader(ppShader); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetPixelShaderConstantF(UINT StartRegister,CONST float* pConstantData,UINT Vector4fCount) override { return real->SetPixelShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPixelShaderConstantF(UINT StartRegister,float* pConstantData,UINT Vector4fCount) override { return real->GetPixelShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetPixelShaderConstantI(UINT StartRegister,CONST int* pConstantData,UINT Vector4iCount) override { return real->SetPixelShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPixelShaderConstantI(UINT StartRegister,int* pConstantData,UINT Vector4iCount) override { return real->GetPixelShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI SetPixelShaderConstantB(UINT StartRegister,CONST BOOL* pConstantData,UINT BoolCount) override { return real->SetPixelShaderConstantB(StartRegister, pConstantData, BoolCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPixelShaderConstantB(UINT StartRegister,BOOL* pConstantData,UINT BoolCount) override { return real->GetPixelShaderConstantB(StartRegister, pConstantData, BoolCount); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawRectPatch(UINT Handle,CONST float* pNumSegs,CONST D3DRECTPATCH_INFO* pRectPatchInfo) override { return real->DrawRectPatch(Handle, pNumSegs, pRectPatchInfo); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DrawTriPatch(UINT Handle,CONST float* pNumSegs,CONST D3DTRIPATCH_INFO* pTriPatchInfo) override { return real->DrawTriPatch(Handle, pNumSegs, pTriPatchInfo); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI DeletePatch(UINT Handle) override { return real->DeletePatch(Handle); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI CreateQuery(D3DQUERYTYPE Type,IDirect3DQuery9** ppQuery) override { return real->CreateQuery(Type, ppQuery); }
};
// 显式IDirect3DSwapChain9代理；自身引用计数与所持系统对象引用分离，释放时只归还拥有的引用。
class SwapProxy final : public IDirect3DSwapChain9 {
public:
    IDirect3DSwapChain9* real; std::atomic<ULONG> references{1};
    // 构造接管一个系统引用；析构归还本层拥有的对象和父接口。
    DeviceProxy* owner; DeviceState display;
    SwapProxy(IDirect3DSwapChain9* value, DeviceProxy* parent) : real(value), owner(parent) { owner->AddRef(); }
    ~SwapProxy() { display.ClearDisplay(); real->Release(); owner->Release(); }
    // 保持本层IUnknown身份；其他扩展接口沿用系统查询结果，不宣称已覆盖其呈现路径。
    HRESULT WINAPI QueryInterface(REFIID riid, void** ppvObj) override { if (!ppvObj) return E_POINTER; if (riid == __uuidof(IUnknown) || riid == __uuidof(IDirect3DSwapChain9)) { *ppvObj = this; AddRef(); return S_OK; } return real->QueryInterface(riid, ppvObj); }
    // 线程安全地增加本代理引用，不干预系统对象内部资源引用。
    ULONG WINAPI AddRef() override { return ++references; }
    // 最后一个本层引用释放时销毁代理，返回剩余引用数。
    ULONG WINAPI Release() override { ULONG left = --references; if (!left) delete this; return left; }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI Present(CONST RECT* pSourceRect,CONST RECT* pDestRect,HWND hDestWindowOverride,CONST RGNDATA* pDirtyRegion,DWORD dwFlags) override;
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetFrontBufferData(IDirect3DSurface9* pDestSurface) override { return real->GetFrontBufferData(pDestSurface); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetBackBuffer(UINT iBackBuffer,D3DBACKBUFFER_TYPE Type,IDirect3DSurface9** ppBackBuffer) override { return real->GetBackBuffer(iBackBuffer, Type, ppBackBuffer); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetRasterStatus(D3DRASTER_STATUS* pRasterStatus) override { return real->GetRasterStatus(pRasterStatus); }
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetDisplayMode(D3DDISPLAYMODE* pMode) override { return real->GetDisplayMode(pMode); }
    // 特殊呈现或父子接口关系由同名手写方法实现并单独说明。
    HRESULT WINAPI GetDevice(IDirect3DDevice9** ppDevice) override;
    // 原样转发系统方法及其所有者校验、错误和资源输出，不伪造成功。
    HRESULT WINAPI GetPresentParameters(D3DPRESENT_PARAMETERS* pPresentationParameters) override { return real->GetPresentParameters(pPresentationParameters); }
};
