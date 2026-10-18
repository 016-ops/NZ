# 在独立 x86 进程核对两个系统 API 的实际模块/RVA 和本机 ACP；不加载游戏或读取玩家资料。
param()
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Requires x86 Windows PowerShell' }
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

// 仅解析下方固定的系统导出，不接受外部 DLL、函数名、进程或内存地址。
public static class NicknameSystemApi {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    static extern IntPtr GetModuleHandle(string name);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, EntryPoint="GetModuleHandleExW")]
    static extern bool GetModuleHandleEx(uint flags, IntPtr address, out IntPtr module);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    static extern uint GetModuleFileName(IntPtr module, StringBuilder name, int size);
    [DllImport("kernel32.dll")]
    public static extern uint GetACP();

    // 获取实际实现模块内偏移，以便跨进程 ASLR 比对；返回值没有玩家数据或运行时绝对地址。
    public static string[] Resolve(bool conversion) {
        string name = conversion ? "MultiByteToWideChar" : "lstrlenA";
        IntPtr address = GetProcAddress(GetModuleHandle("kernel32.dll"), name);
        IntPtr module;
        if (address == IntPtr.Zero || !GetModuleHandleEx(6, address, out module))
            throw new InvalidOperationException("SYSTEM_EXPORT_UNRESOLVED");
        StringBuilder path = new StringBuilder(1024);
        if (GetModuleFileName(module, path, path.Capacity) == 0)
            throw new InvalidOperationException("SYSTEM_MODULE_UNRESOLVED");
        byte[] code = new byte[16];
        Marshal.Copy(address, code, 0, code.Length);
        return new string[] {name, Path.GetFileName(path.ToString()),
            "0x" + (address.ToInt64()-module.ToInt64()).ToString("x"),
            "0x" + address.ToInt64().ToString("x"),
            "0x" + module.ToInt64().ToString("x"), BitConverter.ToString(code).Replace("-", "")};
    }
}
'@
$exports = @()
foreach ($conversion in @($true, $false)) {
    $resolved = [NicknameSystemApi]::Resolve($conversion)
    $exports += @{ name=$resolved[0]; module=$resolved[1]; rva=$resolved[2]; private_code_pointer=$resolved[3]; private_module_base=$resolved[4]; code16=$resolved[5] }
}
@{ kind='X86_FIXED_SYSTEM_API_REFERENCE'; acp=[NicknameSystemApi]::GetACP();
   process_x86=$true; game_loaded=$false; exports=$exports } | ConvertTo-Json -Depth 4
