# 当前进程的 Direct3D9 只读能力诊断：系统 DLL、适配器标识和 HAL/REF 能力，不创建窗口或设备。
param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'D3D9 diagnostic requires 32-bit Windows PowerShell' }
# 证据只写仓库 evidence/ 中的新文件，避免覆盖既有记录或客户端原件。
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$evidenceRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot 'evidence')) + [IO.Path]::DirectorySeparatorChar
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if (-not $resolvedOutput.StartsWith($evidenceRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be inside repository evidence' }
if (Test-Path -LiteralPath $resolvedOutput) { throw 'Evidence file already exists' }

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

// 只从系统目录加载 d3d9.dll，避免当前目录代理 DLL 改变这份环境基线；所有 COM 指针属于本进程。
public static class NativeD3DCapabilityProbe {
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("d3d9.dll", CallingConvention=CallingConvention.StdCall)]
    private static extern IntPtr Direct3DCreate9(uint sdkVersion);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string name);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    private static extern uint GetModuleFileName(IntPtr module, StringBuilder name, uint size);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate uint CountDelegate(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int IdentifierDelegate(IntPtr self, uint adapter, uint flags, IntPtr identifier);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int CapsDelegate(IntPtr self, uint adapter, uint type, IntPtr caps);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate uint ReleaseDelegate(IntPtr self);

    // 每种设备类型单独记录 HRESULT；调用失败时能力保留 null，不能把失败填成不支持着色器。
    public sealed class CapsResult {
        public string Type;
        public string HResult;
        public bool Success;
        public string VertexShaderVersion;
        public string PixelShaderVersion;
        public int? VertexShaderMajor;
        public int? PixelShaderMajor;
        public bool BufferGuardIntact;
    }
    // 标识公开的显示设备型号和驱动版本，不收集序列号、窗口或玩家身份。
    public sealed class AdapterResult {
        public uint Index;
        public string IdentifierHResult;
        public string Driver;
        public string Description;
        public string DeviceName;
        public string DriverVersion;
        public string VendorId;
        public string DeviceId;
        public bool IdentifierBufferGuardIntact;
        public List<CapsResult> Capabilities = new List<CapsResult>();
    }
    // 输出反映这一次进程的能力查询；它不代替游戏自身设备初始化或实际渲染验收。
    public sealed class Result {
        public string Kind = "READ_ONLY_SYSTEM_D3D9_CAPABILITIES";
        public bool ProcessX86;
        public int SessionId;
        public bool Direct3DObjectCreated;
        public string SystemDll;
        public uint AdapterCount;
        public bool WindowCreated = false;
        public bool DeviceCreated = false;
        public bool SystemChanged = false;
        public string Error;
        public List<AdapterResult> Adapters = new List<AdapterResult>();
    }

    // 取得官方 IDirect3D9 虚表的已知槽位；从不调用 CreateDevice 或修改适配器设置。
    private static Delegate Slot(IntPtr instance, int index, Type type) {
        IntPtr table = Marshal.ReadIntPtr(instance);
        return Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(table,index*IntPtr.Size),type);
    }
    // 为输出结构预留有界空间，尾部哨兵检查 API 是否写出预期的 D3D9 结构范围。
    private static IntPtr Buffer(int capacity, int used) {
        IntPtr result = Marshal.AllocHGlobal(capacity);
        byte[] bytes = new byte[capacity];
        for (int i=used;i<capacity;i++) bytes[i]=0xA5;
        Marshal.Copy(bytes,0,result,capacity);
        return result;
    }
    // 返回缓冲区尾部是否完整；不能把越界结果用于解释图形能力。
    private static bool Guard(IntPtr buffer, int used, int capacity) {
        for (int i=used;i<capacity;i++) if (Marshal.ReadByte(buffer,i)!=0xA5) return false;
        return true;
    }
    // 官方 ANSI 固定长度字符数组，最长只读字段自身，拒绝跨字段扫描。
    private static string Text(IntPtr buffer, int offset, int length) {
        byte[] bytes = new byte[length];
        Marshal.Copy(IntPtr.Add(buffer,offset),bytes,0,length);
        int end=Array.IndexOf(bytes,(byte)0);
        return Encoding.Default.GetString(bytes,0,end<0?length:end);
    }
    // DWORD 使用无符号位模式，HRESULT 和着色器版本始终输出完整八位十六进制。
    private static uint Dword(IntPtr buffer, int offset) {
        return unchecked((uint)Marshal.ReadInt32(buffer,offset));
    }
    // SDK_VERSION=32；AdapterIdentifier 的 offset 1056/1064/1068 来自官方固定字符数组布局。
    public static Result Run() {
        Result result = new Result();
        result.ProcessX86=IntPtr.Size==4;
        result.SessionId=Process.GetCurrentProcess().SessionId;
        IntPtr instance=IntPtr.Zero;
        try {
            instance=Direct3DCreate9(32);
            StringBuilder path=new StringBuilder(1024);
            GetModuleFileName(GetModuleHandle("d3d9.dll"),path,1024);
            result.SystemDll=path.ToString();
            result.Direct3DObjectCreated=instance!=IntPtr.Zero;
            if (instance==IntPtr.Zero) { result.Error="DIRECT3D_CREATE9_NULL"; return result; }
            CountDelegate count=(CountDelegate)Slot(instance,4,typeof(CountDelegate));
            IdentifierDelegate identifier=(IdentifierDelegate)Slot(instance,5,typeof(IdentifierDelegate));
            CapsDelegate caps=(CapsDelegate)Slot(instance,14,typeof(CapsDelegate));
            result.AdapterCount=count(instance);
            if (result.AdapterCount>64) throw new InvalidOperationException("ADAPTER_COUNT_OUT_OF_BOUNDS");
            for (uint adapter=0;adapter<result.AdapterCount;adapter++) {
                AdapterResult row=new AdapterResult(); row.Index=adapter;
                IntPtr info=Buffer(2048,1104);
                try {
                    int hr=identifier(instance,adapter,0,info);
                    row.IdentifierHResult="0x"+unchecked((uint)hr).ToString("X8");
                    row.IdentifierBufferGuardIntact=Guard(info,1104,2048);
                    if (hr>=0 && row.IdentifierBufferGuardIntact) {
                        row.Driver=Text(info,0,512); row.Description=Text(info,512,512); row.DeviceName=Text(info,1024,32);
                        uint low=Dword(info,1056), high=Dword(info,1060);
                        row.DriverVersion=(high>>16)+"."+(high&65535)+"."+(low>>16)+"."+(low&65535);
                        row.VendorId="0x"+Dword(info,1064).ToString("X4"); row.DeviceId="0x"+Dword(info,1068).ToString("X4");
                    }
                } finally { Marshal.FreeHGlobal(info); }
                foreach (uint type in new uint[]{1,2}) {
                    IntPtr data=Buffer(512,304);
                    try {
                        int hr=caps(instance,adapter,type,data);
                        CapsResult detail=new CapsResult();
                        detail.Type=type==1?"HAL":"REF"; detail.HResult="0x"+unchecked((uint)hr).ToString("X8");
                        detail.BufferGuardIntact=Guard(data,304,512); detail.Success=hr>=0 && detail.BufferGuardIntact;
                        // D3DCAPS9 的 VertexShaderVersion/PixelShaderVersion 是 offset196/204，不能套用游戏对象偏移。
                        if (detail.Success) {
                            uint vertex=Dword(data,196), pixel=Dword(data,204);
                            detail.VertexShaderVersion="0x"+vertex.ToString("X8"); detail.PixelShaderVersion="0x"+pixel.ToString("X8");
                            detail.VertexShaderMajor=(int)((vertex>>8)&255); detail.PixelShaderMajor=(int)((pixel>>8)&255);
                        }
                        row.Capabilities.Add(detail);
                    } finally { Marshal.FreeHGlobal(data); }
                }
                result.Adapters.Add(row);
            }
        } catch (Exception exception) {
            // 仅记录异常类型，避免将运行环境中的完整文件路径或其他上下文误当作公共错误内容。
            result.Error=exception.GetType().Name;
        } finally {
            if (instance!=IntPtr.Zero) ((ReleaseDelegate)Slot(instance,2,typeof(ReleaseDelegate)))(instance);
        }
        return result;
    }
}
'@
$result = [NativeD3DCapabilityProbe]::Run()
$result | Add-Member -NotePropertyName CapturedAtUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o'))
$result | Add-Member -NotePropertyName Sources -NotePropertyValue @(
    'https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3d9-getdevicecaps',
    'https://learn.microsoft.com/en-us/windows/win32/api/d3d9caps/ns-d3d9caps-d3dcaps9',
    'https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3dadapter-identifier9'
)
$json = $result | ConvertTo-Json -Depth 8
# PowerShell 5.1 的 UTF8 默认带 BOM；显式 UTF8Encoding(false) 保持证据可供 Python/json 直接读取。
[IO.File]::WriteAllText($resolvedOutput, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
$json
