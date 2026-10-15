# 在独立 32 位进程中验证当前 TenProxy 的邮箱交接结构；不启动游戏、注入其他进程或访问腾讯服务。
param([Parameter(Mandatory=$true)][string]$DllPath)
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'This diagnostic requires 32-bit Windows PowerShell' }
# 脚本直接调用时也检查哈希和签名，不能依赖 Python 包装层才阻止错版本 DLL。
if ((Get-FileHash -LiteralPath $DllPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '788bea4e34d6aaea8187df482ea2c859f7a6c8802b226b1ab7c2d9a6a10bc047') { throw 'PROXY_FINGERPRINT_MISMATCH' }
if ((Get-AuthenticodeSignature -LiteralPath $DllPath).Status -ne 'Valid') { throw 'PROXY_SIGNATURE_INVALID' }
# 凭据只经标准输入进入进程内存，不作为命令行、环境变量或临时配置保存。
$inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
$mappingBytes = [Convert]::FromBase64String($inputData.mapping)
if ($mappingBytes.Length -gt 65536) { throw 'Mapping too large' }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

// 只调用已核对当前哈希对应的导出与虚表；ThisCall 的首参数由 .NET 放入 ECX。
public static class EmailProxyProbe {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr LoadLibraryEx(string name, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, SetLastError=true)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32.dll")]
    private static extern bool FreeLibrary(IntPtr module);
    // Invoke 是当前文件的 Cdecl 工厂；实例接口是 ThisCall，不混用 stdcall 的堆栈规则。
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void Factory(int command, ref IntPtr value);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    private delegate int LoginInfo(IntPtr self, IntPtr value);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    private delegate int Release(IntPtr self);

    // 输出只包括结构检查结果；不把原始登录结构或会话令牌交给 PowerShell 输出。
    public sealed class Result {
        public bool FactoryCreated;
        public bool LoginInfoAccepted;
        public uint LocalGameId;
        public uint ResearchServiceId;
        public bool LocalTokenMatches;
        public bool LocalSessionIdMatches;
        public bool OutputGuardIntact;
        public bool ServerRecordVerified;
    }

    // 预留大于观察到最大结构范围的缓冲，尾部哨兵检测意外写越界；所有指针只属于本进程。
    public static Result Run(string path, uint expectedGameId, byte[] expectedToken, byte[] expectedSession, int expectedPort) {
        if (expectedSession.Length!=16 || expectedToken.Length>128) throw new ArgumentException("INPUT_BOUNDS");
        IntPtr module = LoadLibraryEx(path, IntPtr.Zero, 0x1100);
        if (module == IntPtr.Zero) throw new InvalidOperationException("PROXY_LOAD_FAILED");
        IntPtr instance = IntPtr.Zero;
        IntPtr output = Marshal.AllocHGlobal(131072);
        try {
            byte[] clear = new byte[131072];
            for (int i=100000; i<clear.Length; i++) clear[i]=0xA5;
            Marshal.Copy(clear,0,output,clear.Length);
            IntPtr entry = GetProcAddress(module,"Invoke");
            if (entry == IntPtr.Zero) throw new InvalidOperationException("PROXY_EXPORT_MISSING");
            Factory invoke=(Factory)Marshal.GetDelegateForFunctionPointer(entry,typeof(Factory));
            invoke(0x566,ref instance);
            Result result=new Result();
            result.FactoryCreated=instance!=IntPtr.Zero;
            if (!result.FactoryCreated) return result;
            IntPtr table=Marshal.ReadIntPtr(instance);
            LoginInfo read=(LoginInfo)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(table),typeof(LoginInfo));
            result.LoginInfoAccepted=read(instance,output)!=0;
            if (result.LoginInfoAccepted) {
                // 当前游戏实际消费 +0x10 为 Uin、+4 为 ServiceId；不能只按 getter 的写入顺序猜字段。
                result.LocalGameId=unchecked((uint)Marshal.ReadInt32(output,0x10));
                result.ResearchServiceId=unchecked((uint)Marshal.ReadInt32(output,4));
                int keyLength=Marshal.ReadByte(output,0x40);
                bool matches=keyLength==expectedToken.Length && result.LocalGameId==expectedGameId && result.ResearchServiceId==1;
                for (int i=0;i<keyLength && i<expectedToken.Length;i++)
                    matches &= Marshal.ReadByte(output,0x41+i)==expectedToken[i];
                result.LocalTokenMatches=matches;
                // 当前 DLL 的服务器输出偏移来自静态 getter，并以真实原生调用验证，不能凭结构猜测兼容。
                result.ServerRecordVerified=Marshal.ReadInt32(output,0x1270)==(expectedPort==0 ? 0 : 1);
                if (expectedPort!=0) {
                    result.ServerRecordVerified &= Marshal.ReadInt32(output,0x1274)==0;
                    result.ServerRecordVerified &= Marshal.PtrToStringUni(IntPtr.Add(output,0x1480))=="127.0.0.1";
                    result.ServerRecordVerified &= Marshal.ReadInt32(output,0x1580)==expectedPort;
                    result.ServerRecordVerified &= Marshal.PtrToStringUni(IntPtr.Add(output,0x1500))=="127.0.0.1";
                    result.ServerRecordVerified &= Marshal.ReadInt32(output,0x1584)==expectedPort;
                }
                result.LocalSessionIdMatches=expectedSession.Length==16;
                for (int i=0;i<expectedSession.Length;i++)
                    result.LocalSessionIdMatches &= Marshal.ReadByte(output,0x14+i)==expectedSession[i];
            }
            result.OutputGuardIntact=true;
            for (int i=100000;i<131072;i++) result.OutputGuardIntact &= Marshal.ReadByte(output,i)==0xA5;
            return result;
        } finally {
            if (instance!=IntPtr.Zero) {
                IntPtr table=Marshal.ReadIntPtr(instance);
                Release release=(Release)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(table,20),typeof(Release));
                release(instance);
            }
            Marshal.FreeHGlobal(output);
            FreeLibrary(module);
        }
    }
}
'@

$mappingName = [string]$inputData.prefix + [Diagnostics.Process]::GetCurrentProcess().Id
$mapping = [IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew($mappingName, 65536)
$access = $mapping.CreateViewAccessor()
try {
    $access.WriteArray(0, $mappingBytes, 0, $mappingBytes.Length)
    $access.Flush()
    $result = [EmailProxyProbe]::Run($DllPath, [uint32]$inputData.game_id, [Text.Encoding]::ASCII.GetBytes([string]$inputData.game_token), [Convert]::FromBase64String($inputData.session_id), [int]$inputData.loopback_port)
    $result | ConvertTo-Json -Compress
} finally {
    $access.Dispose()
    $mapping.Dispose()
}
