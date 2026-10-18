# 当前版本 TDR 的离线 ABI 验证：仅实例化协议组件并调用元数据/序列化接口，不调用网络 Init。
param([Parameter(Mandatory=$true)][string]$OutputDirectory, [switch]$VerifyResearchResponse, [switch]$VerifyZoneResearchResponse, [switch]$VerifyZoneNotification, [switch]$VerifyZoneLogin, [switch]$VerifyZoneLoginResponse, [switch]$VerifyPlayerDefineEmptyResponse, [switch]$VerifyPlayerInfoResearchResponse)
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Requires x86 Windows PowerShell' }
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$privateRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot 'evidence/private')) + [IO.Path]::DirectorySeparatorChar
$outputRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($privateRoot, [StringComparison]::OrdinalIgnoreCase)) { throw ('Output must be inside private evidence: ' + $outputRoot + ' / ' + $privateRoot) }
if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) { throw 'Wrapper must create the output directory' }
$dll = Join-Path $repositoryRoot 'runtime/native-recovery/coherent-2020/nz/Binaries/Win32/ProtocalHandler.dll'
$tdr = Join-Path $repositoryRoot 'runtime/native-recovery/coherent-2020/nz/TGame/TDR/proto_c2geo.tdr'
$request = Join-Path $repositoryRoot 'evidence/private/email-native-boot-16/application-00.bin'
$expectedTdrHash = '6D75E846532BB506A858323B8A1C91FA67D3ED0C8766F1D1682F9A8D8AFBC8E1'
$zoneLoginMode = $VerifyZoneLogin.IsPresent -or $VerifyZoneLoginResponse.IsPresent -or $VerifyPlayerInfoResearchResponse.IsPresent
$zoneModeCount = [int]($VerifyZoneNotification.IsPresent) + [int]($VerifyZoneLogin.IsPresent) + [int]($VerifyZoneLoginResponse.IsPresent) + [int]($VerifyPlayerDefineEmptyResponse.IsPresent) + [int]($VerifyPlayerInfoResearchResponse.IsPresent)
if ($zoneModeCount -gt 1) { throw 'Select only one captured Zone request mode' }
if ($VerifyZoneNotification.IsPresent -or $zoneLoginMode -or $VerifyPlayerDefineEmptyResponse.IsPresent) {
    if ($VerifyResearchResponse.IsPresent -or $VerifyZoneResearchResponse.IsPresent) { throw 'Zone request modes cannot construct GEO responses' }
    # Zone 模式只接受当前第二连接已捕获的通知，不扩展到任意请求、路径或响应。
    $tdr = Join-Path $repositoryRoot 'runtime/native-recovery/coherent-2020/nz/TGame/TDR/proto_c2zn.tdr'
    $request = Join-Path $repositoryRoot 'evidence/private/email-native-boot-19/application-01.bin'
    $expectedTdrHash = 'C7050A5D78EDB68EB66B2B1E00E68640A762398B1D7C7F9B9072E4C74F915DBA'
    if ($zoneLoginMode) {
        # 单独绑定 boot20 的26字节实际登录请求；只有专项响应开关才构造固定研究返回。
        $request = Join-Path $repositoryRoot 'evidence/private/email-native-boot-20/application-01-sequence-01.bin'
    }
    if ($VerifyPlayerDefineEmptyResponse.IsPresent) {
        # A204 在当前联合体没有正文分支，只允许已观测的8字节包头进行独立验证。
        $request = Join-Path $repositoryRoot 'evidence/private/email-native-boot-22/application-01-sequence-02.bin'
    }
}
# 三个输入均固定来源；拒绝把此工具变成任意 DLL 或任意业务数据执行入口。
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $dllHash = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($dll))).Replace('-','')
    $tdrHash = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($tdr))).Replace('-','')
} finally { $hasher.Dispose() }
if ($dllHash -ne 'E28A7DA8E6668FEB65F5573819714738BCE7210B79C49DFE287A63F2BE3DA228') { throw 'DLL hash mismatch' }
if ($tdrHash -ne $expectedTdrHash) { throw 'TDR hash mismatch' }
[byte[]]$requestBytes = [IO.File]::ReadAllBytes($request)
if ($VerifyZoneNotification.IsPresent -or $zoneLoginMode -or $VerifyPlayerDefineEmptyResponse.IsPresent) {
    $requestHasher = [Security.Cryptography.SHA256]::Create()
    try { $requestHash = [BitConverter]::ToString($requestHasher.ComputeHash($requestBytes)).Replace('-','') } finally { $requestHasher.Dispose() }
    if ($VerifyZoneNotification.IsPresent -and ($requestBytes.Length -ne 11 -or $requestHash -ne '411481F88D63EA7AC96ED7DB0D264D023D53243ACD31F01E0149FB6AF7B469CA')) { throw 'Only captured research Zone notification is accepted' }
    if ($zoneLoginMode -and ($requestBytes.Length -ne 26 -or $requestHash -ne '4C00864169617CF28ADFEDFFC566C8E6867471D1836E6546A44548679B106A38')) { throw 'Only captured Zone login request is accepted' }
    if ($VerifyPlayerDefineEmptyResponse.IsPresent -and ($requestBytes.Length -ne 8 -or $requestHash -ne '5A238CD4B51F13F21306B4C45B14BBDB33D1B40440EC0E64F354870EB0826E67')) { throw 'Only captured empty A204 header is accepted' }
} elseif ($requestBytes.Length -ne 9 -or [BitConverter]::ToString($requestBytes,0,8) -ne '82-02-10-01-00-08-00-01') { throw 'Only captured research PingList request is accepted' }
[byte[]]$zoneRequestBytes = $null
if ($VerifyZoneResearchResponse.IsPresent) {
    $zoneRequestBytes = [IO.File]::ReadAllBytes((Join-Path $repositoryRoot 'evidence/private/email-native-boot-18/application-00-next.bin'))
    $zoneHasher = [Security.Cryptography.SHA256]::Create()
    try { $zoneHash = [BitConverter]::ToString($zoneHasher.ComputeHash($zoneRequestBytes)).Replace('-','') } finally { $zoneHasher.Dispose() }
    if ($zoneRequestBytes.Length -ne 40 -or $zoneHash -ne '4E9B0F9151539016D31E14D3B0957340F1E2ECD60CB5708530A0AB8165AD44A3') { throw 'Only captured research ZoneList request is accepted' }
}
foreach ($name in @('worker.json','ping-response-empty.bin','ping-response-loopback.bin')) {
    if (Test-Path -LiteralPath (Join-Path $outputRoot $name)) { throw 'Output already exists' }
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

// 仅调用当前组件公开工厂与工厂对象的固定虚表槽；不接受任意 RVA、路径、元数据名称。
public static class CurrentGeoTdrProbe {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, SetLastError=true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr VirtualAlloc(IntPtr address, UIntPtr size, uint allocation, uint protect);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool VirtualProtect(IntPtr address, UIntPtr size, uint protect, out uint old);
    [DllImport("kernel32.dll")] static extern bool VirtualFree(IntPtr address, UIntPtr size, uint operation);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void Factory(int command, out IntPtr result);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int LoadMeta(IntPtr self, out IntPtr lib, IntPtr bytes, int length);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr FindMeta(IntPtr self, IntPtr lib, IntPtr name);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int ConvertMeta(IntPtr self, IntPtr meta, IntPtr output, IntPtr input, int version);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void FreeMeta(IntPtr self, ref IntPtr lib);

    // 前后不可访问页和紧邻数据的哨兵限制本进程缓冲，越界异常交给外层 Job 处理。
    sealed class Guard : IDisposable {
        IntPtr allocation; public IntPtr Data; public int Size; int total;
        // 分配限定容量及保护页；所有数据页初始为零，指针仅在本离线进程内使用。
        public Guard(int size) {
            // 当前 Zone 元数据 3024238 字节；单缓冲硬上限 4 MiB，仍拒绝任意大分配。
            if(size<1 || size>4*1024*1024) throw new ArgumentOutOfRangeException("size");
            Size=size; int middle=((size+64+4095)/4096)*4096; total=middle+8192;
            allocation=VirtualAlloc(IntPtr.Zero,(UIntPtr)total,0x3000,4);
            if(allocation==IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            uint old;
            if(!VirtualProtect(allocation,(UIntPtr)4096,1,out old) || !VirtualProtect(IntPtr.Add(allocation,4096+middle),(UIntPtr)4096,1,out old)) throw new System.ComponentModel.Win32Exception();
            Data=IntPtr.Add(allocation,4096+32);
            for(int i=0;i<32;i++) { Marshal.WriteByte(Data,i-32,0xA5); Marshal.WriteByte(Data,size+i,0x5A); }
        }
        // 校验前后紧邻哨兵，发现任何越界即使原生接口返回零也拒绝验收。
        public void Check() { for(int i=0;i<32;i++) if(Marshal.ReadByte(Data,i-32)!=0xA5 || Marshal.ReadByte(Data,Size+i)!=0x5A) throw new InvalidOperationException("Buffer canary changed"); }
        // 仅复制容量范围内的返回数据，拒绝组件提供的异常长度。
        public byte[] Read(int n) { if(n<0||n>Size) throw new InvalidOperationException("Invalid result size"); Check(); byte[] a=new byte[n];Marshal.Copy(Data,a,0,n);return a; }
        // 将固定研究输入写入自有缓冲，不扩容或写入其他进程。
        public void Put(byte[] a) { if(a.Length>Size)throw new InvalidOperationException("Input too large");Marshal.Copy(a,0,Data,a.Length); }
        // 释放本对象唯一分配，重复释放安全；外层进程仍有 Job 超时兜底。
        public void Dispose() { if(allocation!=IntPtr.Zero){VirtualFree(allocation,UIntPtr.Zero,0x8000);allocation=IntPtr.Zero;} }
    }
    // 两 DWORD 的当前 TDR buffer: 数据指针和输入容量/返回长度。所有调用后核对边界与返回值。
    static byte[] Convert(IntPtr self, IntPtr meta, ConvertMeta fn, byte[] input, int capacity, string label, List<object> checks) {
        using(Guard src=new Guard(input.Length)) using(Guard dst=new Guard(capacity)) using(Guard sb=new Guard(8)) using(Guard db=new Guard(8)) {
            src.Put(input);Marshal.WriteIntPtr(sb.Data,src.Data);Marshal.WriteInt32(sb.Data,4,input.Length);
            Marshal.WriteIntPtr(db.Data,dst.Data);Marshal.WriteInt32(db.Data,4,capacity);
            int code=fn(self,meta,db.Data,sb.Data,0);
            src.Check();dst.Check();sb.Check();db.Check();
            int n=Marshal.ReadInt32(db.Data,4);int consumed=Marshal.ReadInt32(sb.Data,4);
            checks.Add(new Dictionary<string,object>{{"step",label},{"return_code",code},{"input_length_before",input.Length},{"output_capacity",capacity},{"output_length",n},{"input_length_after",consumed},{"canaries",true}});
            // 正确转换槽返回状态码；此前 0x10106F04 是误调错误文本槽返回的指针，不是成功状态。
            if(code!=0)throw new InvalidOperationException(label+" native error "+code);
            return dst.Read(n);
        }
    }
    // 调用者先核对固定槽的 RVA，再以已静态证明的 x86 thiscall 签名取得委托。
    static T Slot<T>(IntPtr self,int offset) { return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(self),offset),typeof(T)); }
    // 在调用前核对实际虚表地址，防止相邻槽签名不同而造成静默错误；动态指针仅写入私有证据。
    static void CheckSlot(IntPtr module,IntPtr self,int offset,int expected,List<object> checks) {
        IntPtr address=Marshal.ReadIntPtr(Marshal.ReadIntPtr(self),offset);long rva=address.ToInt64()-module.ToInt64();
        checks.Add(new Dictionary<string,object>{{"step","vtable_slot"},{"slot",String.Format("0x{0:X}",offset)},{"function_rva",String.Format("0x{0:X}",rva)}});
        if(rva!=expected)throw new InvalidOperationException("Unexpected vtable implementation");
    }
    // 计算证据摘要；输入只有固定请求和无账号的研究响应。
    static string Sha(byte[] a){using(SHA256 h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(a)).Replace("-","").ToLowerInvariant();}
    // 按当前 x86 主机布局写入 16 位元数据字段，不代表线上字节序。
    static void U16(byte[] a,int at,int v){a[at]=(byte)v;a[at+1]=(byte)(v>>8);}
    // 按当前 x86 主机布局写入 32 位字段，线上编码由当前 DLL 决定。
    static void U32(byte[] a,int at,int v){Buffer.BlockCopy(BitConverter.GetBytes(v),0,a,at,4);}
    // 独立读取线上包头大端字段，避免仅用同一编码器自往返就判定包头有效。
    static int Be16(byte[] a,int at){return (a[at]<<8)|a[at+1];}
    // 以无符号原始DWORD核对当前已捕获登录字段；保留FFFFFFFF，不推断子频道哨兵的业务含义。
    static uint Be32(byte[] a,int at){return ((uint)a[at]<<24)|((uint)a[at+1]<<16)|((uint)a[at+2]<<8)|a[at+3];}
    // 仅构造明确标注的研究 PingList 响应：零端点或本机端点，不推断生产规则。
    static byte[] ResearchResponse(bool one) {
        byte[] host=new byte[26906];U16(host,0,0x8202);U16(host,2,0x2001);U16(host,4,8);
        U16(host,8,0x8B00);U16(host,10,one?1:0);
        if(one){U32(host,12,0);host[16]=127;host[19]=1;byte[] name=Encoding.ASCII.GetBytes("127.0.0.1");Buffer.BlockCopy(name,0,host,20,name.Length);U32(host,148,0);}
        U32(host,26892,1000);U32(host,26896,0);U32(host,26900,0);U16(host,26904,0);return host;
    }
    // 只检验已捕获的本机 ZoneList 请求及固定单条研究响应；不建立或伪造 Zone 登录会话。
    static void ValidateZone(IntPtr self,IntPtr lib,IntPtr meta,ConvertMeta pack,ConvertMeta unpack,byte[] request,string output,Dictionary<string,object> result,List<object> checks) {
        byte[] requestHost=Convert(self,meta,unpack,request,26906,"zone_request_unpack",checks);
        byte[] requestAgain=Convert(self,meta,pack,requestHost,32768,"zone_request_repack",checks);
        if(Sha(request)!=Sha(requestAgain))throw new InvalidOperationException("Zone request roundtrip changed");
        result["zone_request"]=new Dictionary<string,object>{{"bytes",request.Length},{"sha256",Sha(request)},{"roundtrip_equal",true}};
        byte[] host=new byte[26906];U16(host,0,0x8202);U16(host,2,0x2000);U16(host,4,8);U16(host,8,0x8B00);U16(host,10,1);
        // Ipv4 沿用本次实际 Ping 请求返回的主机字节；域名和端口均为显式本机研究端点。
        host[12]=127;host[15]=1;byte[] domain=Encoding.ASCII.GetBytes("127.0.0.1");Buffer.BlockCopy(domain,0,host,16,domain.Length);
        U16(host,144,45678);U32(host,146,1);U32(host,150,1);U32(host,154,1);U16(host,2732,1);
        // LoadExts 是固定 6 个 32 位字段，零初始化保留全部；DecisionMethod=1 为显式顺序研究选择。
        IntPtr name=Marshal.StringToHGlobalAnsi("GEO2C_ResZoneList");IntPtr bodyMeta;
        try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
        if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("No GEO2C_ResZoneList");
        byte[] bodyHost=new byte[2726];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
        byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,"zone_body_pack",checks);U16(host,6,body.Length);
        byte[] wire=Convert(self,meta,pack,host,32768,"zone_response_pack",checks);
        if(wire.Length<8 || Be16(wire,0)!=0x8202 || Be16(wire,2)!=0x2000 || Be16(wire,4)!=8 || Be16(wire,6)!=wire.Length-8)throw new InvalidOperationException("Zone response header inconsistent");
        byte[] framedBody=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framedBody,0,framedBody.Length);
        if(Sha(body)!=Sha(framedBody))throw new InvalidOperationException("Zone standalone body disagrees");
        byte[] decoded=Convert(self,meta,unpack,wire,26906,"zone_response_unpack",checks);
        if(Sha(host)!=Sha(decoded))throw new InvalidOperationException("Zone host fields disagree");
        byte[] repacked=Convert(self,meta,pack,decoded,32768,"zone_response_repack",checks);
        if(Sha(wire)!=Sha(repacked))throw new InvalidOperationException("Zone response roundtrip changed");
        File.WriteAllBytes(Path.Combine(output,"zone-response-loopback.bin"),wire);
        result["zone_response"]=new Dictionary<string,object>{{"bytes",wire.Length},{"body_length",body.Length},{"sha256",Sha(wire)},{"roundtrip_equal",true},{"decoded_host_equal",true},{"header_lengths_equal",true},{"standalone_body_equal",true},{"file","zone-response-loopback.bin"}};
    }
    // 当前消费者已证明 8100 分支；其余字段为明确标注的单服研究值，不接真实钱包或角色。
    static void ValidateLoginResponse(IntPtr self,IntPtr lib,IntPtr meta,ConvertMeta pack,ConvertMeta unpack,string output,Dictionary<string,object> result,List<object> checks) {
        byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA001);U16(host,4,8);U16(host,8,0x8100);U32(host,10,1);U32(host,14,1);
        // ServerTime=0 在当前代码跳过时间转换；两币、赠品计数、IsNewbie、AreaId 全部为零研究值。
        IntPtr name=Marshal.StringToHGlobalAnsi("ZN2C_ResLogin");IntPtr bodyMeta;
        try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
        if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("No ZN2C_ResLogin");
        byte[] bodyHost=new byte[17953];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
        byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,"login_response_body_pack",checks);
        if(body.Length>65535)throw new InvalidOperationException("Login response body too large");U16(host,6,body.Length);
        byte[] wire=Convert(self,meta,pack,host,32768,"login_response_pack",checks);
        if(wire.Length<8 || Be16(wire,0)!=0x3243 || Be16(wire,2)!=0xA001 || Be16(wire,4)!=8 || Be16(wire,6)!=wire.Length-8)throw new InvalidOperationException("Login response header inconsistent");
        byte[] framedBody=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framedBody,0,framedBody.Length);
        if(Sha(body)!=Sha(framedBody))throw new InvalidOperationException("Login standalone body disagrees");
        byte[] decoded=Convert(self,meta,unpack,wire,1025331,"login_response_unpack",checks);
        if(Sha(host)!=Sha(decoded))throw new InvalidOperationException("Login decoded host differs from fixed research profile");
        byte[] again=Convert(self,meta,pack,decoded,32768,"login_response_repack",checks);
        if(Sha(wire)!=Sha(again))throw new InvalidOperationException("Login response roundtrip changed");
        File.WriteAllBytes(Path.Combine(output,"zone-login-response-research.bin"),wire);
        result["login_response"]=new Dictionary<string,object>{{"profile","cn2020-zone-login-success-research-v1"},{"bytes",wire.Length},{"body_length",body.Length},{"sha256",Sha(wire)},{"roundtrip_equal",true},{"decoded_host_equal",true},{"header_lengths_equal",true},{"standalone_body_equal",true},{"network_reply_sent",false},{"real_account_assets_used",false},{"file","zone-login-response-research.bin"}};
        // 只更改64位ServerTime，使用超过2038年的值验证高位和端序；不改零时刻原夹具。
        Buffer.BlockCopy(BitConverter.GetBytes((long)2208988800),0,host,18,8);
        wire=Convert(self,meta,pack,host,32768,"login_clock_pack",checks);
        decoded=Convert(self,meta,unpack,wire,1025331,"login_clock_unpack",checks);
        again=Convert(self,meta,pack,decoded,32768,"login_clock_repack",checks);
        if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||wire.Length!=41)throw new InvalidOperationException("Login clock differs");
        File.WriteAllBytes(Path.Combine(output,"zone-login-clock.bin"),wire);
        result["clock_login_fixture"]=new Dictionary<string,object>{{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}};
        // NZ/GP为当前有符号32位字段，验证兼容上限不会被截断或变成负数。
        U32(host,26,int.MaxValue);U32(host,30,int.MaxValue);
        wire=Convert(self,meta,pack,host,32768,"login_wallet_pack",checks);
        decoded=Convert(self,meta,unpack,wire,1025331,"login_wallet_unpack",checks);
        again=Convert(self,meta,pack,decoded,32768,"login_wallet_repack",checks);
        if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again))throw new InvalidOperationException("Login wallet differs");
        File.WriteAllBytes(Path.Combine(output,"zone-login-wallet.bin"),wire);
        result["wallet_login_fixture"]=new Dictionary<string,object>{{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}};
    }
    // A205 的零长度路径已在当前消费者证明；只生成“无自定义数据”，不制造非空版本结构。
    static void ValidateDefineEmptyResponse(IntPtr self,IntPtr lib,ConvertMeta pack,ConvertMeta unpack,string output,Dictionary<string,object> result,List<object> checks) {
        IntPtr bodyName=Marshal.StringToHGlobalAnsi("ZN2C_ResGetPlayerDefineInfo"),packageName=Marshal.StringToHGlobalAnsi("C2ZNPkg");IntPtr bodyMeta,meta;
        try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,bodyName);meta=Slot<FindMeta>(self,0x58)(self,lib,packageName);}finally{Marshal.FreeHGlobal(bodyName);Marshal.FreeHGlobal(packageName);}
        if(bodyMeta==IntPtr.Zero || meta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed A205 schema");
        byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA205);U16(host,4,8);U16(host,8,0x8100);
        byte[] bodyHost=new byte[3206];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
        byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,"define_empty_body_pack",checks);U16(host,6,body.Length);
        byte[] wire=Convert(self,meta,pack,host,32768,"define_empty_response_pack",checks);
        if(wire.Length<8 || Be16(wire,0)!=0x3243 || Be16(wire,2)!=0xA205 || Be16(wire,4)!=8 || Be16(wire,6)!=wire.Length-8)throw new InvalidOperationException("A205 header inconsistent");
        byte[] framedBody=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framedBody,0,framedBody.Length);
        if(Sha(body)!=Sha(framedBody))throw new InvalidOperationException("A205 standalone body disagrees");
        byte[] decoded=Convert(self,meta,unpack,wire,1025331,"define_empty_response_unpack",checks);
        if(Sha(host)!=Sha(decoded))throw new InvalidOperationException("A205 decoded host differs from empty profile");
        byte[] again=Convert(self,meta,pack,decoded,32768,"define_empty_response_repack",checks);
        if(Sha(wire)!=Sha(again))throw new InvalidOperationException("A205 roundtrip changed");
        File.WriteAllBytes(Path.Combine(output,"player-define-empty-response.bin"),wire);
        result["define_empty_response"]=new Dictionary<string,object>{{"profile","cn2020-player-define-empty-research-v1"},{"bytes",wire.Length},{"body_length",body.Length},{"sha256",Sha(wire)},{"roundtrip_equal",true},{"decoded_host_equal",true},{"header_lengths_equal",true},{"standalone_body_equal",true},{"network_reply_sent",false},{"nonempty_data_constructed",false},{"file","player-define-empty-response.bin"}};
    }
    // 固定工厂创建对象后只调用 TDR 槽：先核对真实请求，再按显式选项检查研究响应。
    // 成功要求状态码零、所有保护哨兵不变、长度一致、完整主机字段及线上字节往返一致。
    // 对固定的测试身份/ASCII与CP936中文昵称构造资料夹具，不用真实账号，不声明零值是可用游戏规则。
    // 各夹具独立正文打包、完整host比较和重打包，避免仅靠wire长度或一个常量样本误判。
    static void ValidatePlayerInfoProfile(IntPtr self,IntPtr lib,IntPtr meta,ConvertMeta pack,ConvertMeta unpack,string output,Dictionary<string,object> result,List<object> checks) {
        var fixtures=new List<object>();
        for(int index=0;index<7;index++) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA005);U16(host,4,8);U16(host,8,0x8100);
            byte[] uin=BitConverter.GetBytes((ulong)(10001+index));Buffer.BlockCopy(uin,0,host,10,8);
            byte[] nickname=index==2?new byte[]{0xd6,0xd0,0xce,0xc4,0xea,0xc7,0xb3,0xc6}:Encoding.ASCII.GetBytes(index==0?"NZResearchA":"NZProfileFixtureB");Buffer.BlockCopy(nickname,0,host,22,nickname.Length);
            // PlayerInfo host昵称容量32；Level+160只是本夹具值1，角色/币值/时间等未核验字段保持0并披露。
            U32(host,170,1);
            // 第六个独立夹具验证PlayerInfo+378的容量字段，宿主包含10字节前缀。
            if(index==5)U32(host,388,1000000);
            // 第七个夹具验证64位战队数字ID，不能只验证零值位置。
            if(index==6)Buffer.BlockCopy(BitConverter.GetBytes((ulong)4294967301),0,host,120,8);
            // 两个额外夹具验证七类钱包：小值与各字段/数据库交集上限，不修改经验或VIP。
            if(index>=3) {
                foreach(int offset in new int[]{54,58,62,66,80})U32(host,offset,index==3?123456:int.MaxValue);
                U16(host,306,index==3?123:65535);
                Buffer.BlockCopy(BitConverter.GetBytes(index==3?(ulong)123456789:(ulong)long.MaxValue),0,host,330,8);
            }
            IntPtr name=Marshal.StringToHGlobalAnsi("ZN2C_NtfPlayerInfo");IntPtr bodyMeta;
            try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
            if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed PlayerInfo schema");
            byte[] bodyHost=new byte[1412];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
            string label="player_info_fixture_"+index;
            byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,label+"_body_pack",checks);U16(host,6,body.Length);
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            if(wire.Length<8||Be16(wire,0)!=0x3243||Be16(wire,2)!=0xA005||Be16(wire,4)!=8||Be16(wire,6)!=wire.Length-8)throw new InvalidOperationException("PlayerInfo header inconsistent");
            byte[] framedBody=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framedBody,0,framedBody.Length);
            if(Sha(body)!=Sha(framedBody))throw new InvalidOperationException("PlayerInfo standalone body differs");
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            if(Sha(host)!=Sha(decoded))throw new InvalidOperationException("PlayerInfo host differs");
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            if(Sha(wire)!=Sha(again))throw new InvalidOperationException("PlayerInfo roundtrip differs");
            string filename=label+".bin";File.WriteAllBytes(Path.Combine(output,filename),wire);
            fixtures.Add(new Dictionary<string,object>{{"file",filename},{"bytes",wire.Length},{"body_length",body.Length},{"sha256",Sha(wire)},{"fixture_game_id",10001+index},{"nickname_bytes",nickname.Length},{"nickname_encoding",index==2?"CP936":"ASCII"},{"roundtrip_equal",true},{"decoded_host_equal",true},{"standalone_body_equal",true}});
        }
        result["player_info_fixtures"]=fixtures;
        // 当前A006的355B完整正文host：Result、IsLastPkg、PropCount及5个70B槽。
        // 这里只构造零道具终包，不创建资源/GID/角色，也不发送网络数据。
        byte[] propsHost=new byte[1025331];U16(propsHost,0,0x3243);U16(propsHost,2,0xA006);U16(propsHost,4,8);
        U16(propsHost,8,0x8100);propsHost[10]=1;U16(propsHost,11,0);
        IntPtr propsName=Marshal.StringToHGlobalAnsi("ZN2C_NtfPlayerProps");IntPtr propsMeta;
        try{propsMeta=Slot<FindMeta>(self,0x58)(self,lib,propsName);}finally{Marshal.FreeHGlobal(propsName);}
        if(propsMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed PlayerProps schema");
        byte[] propsBodyHost=new byte[355];Buffer.BlockCopy(propsHost,8,propsBodyHost,0,355);
        byte[] propsBody=Convert(self,propsMeta,pack,propsBodyHost,32768,"empty_props_body_pack",checks);U16(propsHost,6,propsBody.Length);
        byte[] propsWire=Convert(self,meta,pack,propsHost,32768,"empty_props_pack",checks);
        byte[] propsDecoded=Convert(self,meta,unpack,propsWire,1025331,"empty_props_unpack",checks);
        byte[] propsAgain=Convert(self,meta,pack,propsDecoded,32768,"empty_props_repack",checks);
        byte[] propsFramedBody=new byte[propsWire.Length-8];Buffer.BlockCopy(propsWire,8,propsFramedBody,0,propsFramedBody.Length);
        if(Sha(propsHost)!=Sha(propsDecoded)||Sha(propsWire)!=Sha(propsAgain)||Sha(propsBody)!=Sha(propsFramedBody))
            throw new InvalidOperationException("Empty PlayerProps serialization mismatch");
        File.WriteAllBytes(Path.Combine(output,"player-props-empty.bin"),propsWire);
        result["empty_player_props"]=new Dictionary<string,object>{{"profile","cn2020-player-props-empty-research-v1"},{"bytes",propsWire.Length},{"sha256",Sha(propsWire)},{"host_bytes",355},{"roundtrip_equal",true},{"decoded_host_equal",true},{"standalone_body_equal",true}};
        // 非空数组只验证字段和端序，不声明下面的测试数值构成有效角色或真实拥有的资产。
        // 使用非零64位标志、负有效期、两件不同GID及不同末包值，发现单条/符号位/步长错误。
        var nonemptyProps=new List<object>();
        for(int fixture=0;fixture<2;fixture++) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA006);U16(host,4,8);
            U16(host,8,0x8100);host[10]=(byte)fixture;U16(host,11,2);
            for(int i=0;i<2;i++) {
                int at=13+i*70;
                Buffer.BlockCopy(BitConverter.GetBytes((ulong)10001),0,host,at,8);
                Buffer.BlockCopy(BitConverter.GetBytes((ulong)(1001+i)),0,host,at+8,8);
                U32(host,at+16,i==0?100014:100016);
                Buffer.BlockCopy(BitConverter.GetBytes(0x123456789abcdef0UL),0,host,at+20,8);
                U32(host,at+28,1);Buffer.BlockCopy(BitConverter.GetBytes((long)12345),0,host,at+32,8);
                U32(host,at+40,-1);U32(host,at+44,123);U32(host,at+48,456);
                host[at+60]=(byte)(10+i);U32(host,at+61,876000);U32(host,at+65,17);host[at+69]=48;
            }
            byte[] bodyHost=new byte[355];Buffer.BlockCopy(host,8,bodyHost,0,355);
            string label="nonempty_props_"+fixture;
            byte[] body=Convert(self,propsMeta,pack,bodyHost,32768,label+"_body_pack",checks);U16(host,6,body.Length);
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            byte[] framed=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framed,0,framed.Length);
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||Sha(body)!=Sha(framed))throw new InvalidOperationException("Nonempty PropInfo fixture differs");
            string file=label+".bin";File.WriteAllBytes(Path.Combine(output,file),wire);
            nonemptyProps.Add(new Dictionary<string,object>{{"file",file},{"bytes",wire.Length},{"sha256",Sha(wire)},{"is_last",fixture==1},{"prop_count",2},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true}});
        }
        result["nonempty_player_props_fixtures"]=nonemptyProps;
        // 44轮实际A008是Operation0、Subject1001、Target1、Location10。
        // 本固定A009只验证编码，8100是否成功另由当前消费者判断；不发送网络数据。
        byte[] equipHost=new byte[1025331];U16(equipHost,0,0x3243);U16(equipHost,2,0xA009);U16(equipHost,4,8);
        U16(equipHost,8,0x8100);U16(equipHost,10,0);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)1001),0,equipHost,12,8);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)1),0,equipHost,20,8);equipHost[28]=10;
        IntPtr equipName=Marshal.StringToHGlobalAnsi("ZN2C_ResPropOperation");IntPtr equipMeta;
        try{equipMeta=Slot<FindMeta>(self,0x58)(self,lib,equipName);}finally{Marshal.FreeHGlobal(equipName);}
        if(equipMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed PropOperation schema");
        byte[] equipBodyHost=new byte[21];Buffer.BlockCopy(equipHost,8,equipBodyHost,0,21);
        byte[] equipBody=Convert(self,equipMeta,pack,equipBodyHost,32768,"equip_body_pack",checks);U16(equipHost,6,equipBody.Length);
        byte[] equipWire=Convert(self,meta,pack,equipHost,32768,"equip_pack",checks);
        byte[] equipDecoded=Convert(self,meta,unpack,equipWire,1025331,"equip_unpack",checks);
        byte[] equipAgain=Convert(self,meta,pack,equipDecoded,32768,"equip_repack",checks);
        byte[] equipFramed=new byte[equipWire.Length-8];Buffer.BlockCopy(equipWire,8,equipFramed,0,equipFramed.Length);
        if(Sha(equipHost)!=Sha(equipDecoded)||Sha(equipWire)!=Sha(equipAgain)||Sha(equipBody)!=Sha(equipFramed))throw new InvalidOperationException("PropOperation fixture differs");
        File.WriteAllBytes(Path.Combine(output,"role-equip-response.bin"),equipWire);
        result["role_equip_response_fixture"]=new Dictionary<string,object>{{"bytes",equipWire.Length},{"sha256",Sha(equipWire)},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true},{"result_success_semantics_verified",false}};
        // 当前TDR错误码：坏GID=0108、类型不匹配=0109；失败应答仍带原19B请求，绝不发成功通知。
        var rejectedFixtures=new List<object>();
        foreach(int failure in new int[]{0x0108,0x0109}) {
            byte[] host=(byte[])equipHost.Clone();U16(host,8,failure);
            Buffer.BlockCopy(BitConverter.GetBytes((ulong)3816),0,host,12,8);
            Buffer.BlockCopy(BitConverter.GetBytes((ulong)3865),0,host,20,8);host[28]=255;
            string label="equipment-rejected-"+failure;
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||wire.Length!=29)throw new InvalidOperationException("Equipment failure fixture differs");
            File.WriteAllBytes(Path.Combine(output,label+".bin"),wire);
            rejectedFixtures.Add(new Dictionary<string,object>{{"code",failure},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}});
        }
        result["equipment_rejected_fixtures"]=rejectedFixtures;
        // 当前A00B无Result；使用真实样式武器/背包GID核对完整19B通知，禁止套用其他区域帧。
        byte[] notifyHost=new byte[1025331];U16(notifyHost,0,0x3243);U16(notifyHost,2,0xA00B);U16(notifyHost,4,8);
        U16(notifyHost,6,19);U16(notifyHost,8,0);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)5019),0,notifyHost,10,8);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)1055),0,notifyHost,18,8);notifyHost[26]=0;
        byte[] notifyWire=Convert(self,meta,pack,notifyHost,32768,"equip_notify_pack",checks);
        byte[] notifyDecoded=Convert(self,meta,unpack,notifyWire,1025331,"equip_notify_unpack",checks);
        byte[] notifyAgain=Convert(self,meta,pack,notifyDecoded,32768,"equip_notify_repack",checks);
        if(Sha(notifyHost)!=Sha(notifyDecoded)||Sha(notifyWire)!=Sha(notifyAgain)||notifyWire.Length!=27)throw new InvalidOperationException("PropOperation notification differs");
        File.WriteAllBytes(Path.Combine(output,"equip-notification.bin"),notifyWire);
        result["equip_notification_fixture"]=new Dictionary<string,object>{{"bytes",notifyWire.Length},{"sha256",Sha(notifyWire)},{"host_equal",true},{"roundtrip_equal",true}};
        // 当前商城A362使用SHOP_ERR_SUCC=8200；空URL表示无需下载，哈希用有符号边界夹具核对。
        var shopFixtures=new List<object>();
        foreach(int shopHash in new int[]{0,0x12345678,-1}) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA362);U16(host,4,8);
            U16(host,8,0x8200);U32(host,10,shopHash);
            IntPtr shopName=Marshal.StringToHGlobalAnsi("ZN2C_ResShopConfHash");IntPtr shopMeta;
            try{shopMeta=Slot<FindMeta>(self,0x58)(self,lib,shopName);}finally{Marshal.FreeHGlobal(shopName);}
            if(shopMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed shop schema");
            byte[] bodyHost=new byte[262];Buffer.BlockCopy(host,8,bodyHost,0,262);
            string label="shop_hash_"+shopHash;
            byte[] body=Convert(self,shopMeta,pack,bodyHost,32768,label+"_body_pack",checks);U16(host,6,body.Length);
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again))throw new InvalidOperationException("Shop hash fixture differs");
            File.WriteAllBytes(Path.Combine(output,label+".bin"),wire);
            shopFixtures.Add(new Dictionary<string,object>{{"file",label+".bin"},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}});
        }
        result["shop_hash_fixtures"]=shopFixtures;
        // 当前A50E余额查询对应属性通知：TP标志1、TPBALANCE原因42均来自当前宏表。
        // 保留全部嵌套计数为0，不附带道具、经验或天赋变更。
        var balanceFixtures=new List<object>();
        foreach(int balance in new int[]{0,123456,int.MaxValue}) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA00A);U16(host,4,8);
            U32(host,8,1);U16(host,12,42);U32(host,14,balance);
            // 当前TDR解码将空TalentSkillInfoEx.Version规范化为3；显式采用该值核对全host，未置天赋变更标志。
            host[7248]=3;
            string label="tp_balance_"+balance;
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_initial_pack",checks);U16(host,6,wire.Length-8);
            wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            var hostDifferences=new List<object>();
            for(int d=0;d<host.Length&&hostDifferences.Count<20;d++)if(host[d]!=decoded[d])hostDifferences.Add(new Dictionary<string,object>{{"offset",d},{"before",host[d]},{"after",decoded[d]}});
            result["tp_balance_diagnostic"]=new Dictionary<string,object>{{"host_differences",hostDifferences},{"wire_equal",Sha(wire)==Sha(again)},{"bytes",wire.Length}};
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||wire.Length!=70)throw new InvalidOperationException("TP balance notification differs");
            File.WriteAllBytes(Path.Combine(output,label+".bin"),wire);
            balanceFixtures.Add(new Dictionary<string,object>{{"file",label+".bin"},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}});
        }
        result["tp_balance_fixtures"]=balanceFixtures;
        // 当前A358是详细频道数组，不是PH版本的短字符串。只构造一个区服/一个频道的回环夹具。
        // Normal=2来自当前宏表；容量/人数/显示序号为本地研究值，不是对局人数上限。
        var detailedChannels=new List<object>();
        foreach(int port in new int[]{19001,45678}) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,0xA358);U16(host,4,8);
            U16(host,8,0x8100);U16(host,10,1);int detail=12;U16(host,detail,1);U16(host,detail+2,2);
            byte[] title=Encoding.ASCII.GetBytes("NZ Online");Buffer.BlockCopy(title,0,host,detail+4,title.Length);
            U32(host,detail+36,100);U32(host,detail+40,1);U16(host,detail+44,1);
            int zone=detail+46;U32(host,zone,0x0100007f);U16(host,zone+4,port);U16(host,zone+6,1);U16(host,zone+8,1);
            byte[] prefix=Encoding.ASCII.GetBytes("NZ");Buffer.BlockCopy(prefix,0,host,zone+10,prefix.Length);U32(host,zone+42,1);
            IntPtr name=Marshal.StringToHGlobalAnsi("ZN2C_ResDetailedMainChnlList");IntPtr bodyMeta;
            try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
            if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed detailed main channel schema");
            byte[] bodyHost=new byte[96604];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
            string label="detailed_channel_"+port;
            byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,label+"_body_pack",checks);U16(host,6,body.Length);
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            byte[] framed=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framed,0,framed.Length);
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||Sha(body)!=Sha(framed))throw new InvalidOperationException("Detailed main channel fixture differs");
            string file=label+".bin";File.WriteAllBytes(Path.Combine(output,file),wire);
            detailedChannels.Add(new Dictionary<string,object>{{"file",file},{"port",port},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true}});
        }
        result["detailed_main_channel_fixtures"]=detailedChannels;
        // 设置正文始终不透明；0/104/3200覆盖当前实收长度与上限，不赋予内部版本语义。
        var defineFixtures=new List<object>();
        foreach(int count in new int[]{0,104,3200}) {
            foreach(int command in new int[]{0xA202,0xA205}) {
                byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,command);U16(host,4,8);
                bool response=command==0xA205;int offset=response?14:12;
                if(response)U16(host,8,0x8100);U32(host,response?10:8,count);
                for(int i=0;i<count;i++)host[offset+i]=(byte)(i%256);
                string schema=response?"ZN2C_ResGetPlayerDefineInfo":"C2ZN_ReqSetPlayerDefineInfo";
                IntPtr name=Marshal.StringToHGlobalAnsi(schema);IntPtr bodyMeta;
                try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
                if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed settings schema");
                byte[] bodyHost=new byte[response?3206:3204];Buffer.BlockCopy(host,8,bodyHost,0,bodyHost.Length);
                string label="player_define_"+command.ToString("x")+"_"+count;
                byte[] body=Convert(self,bodyMeta,pack,bodyHost,32768,label+"_body_pack",checks);U16(host,6,body.Length);
                byte[] wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
                byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
                byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
                byte[] framed=new byte[wire.Length-8];Buffer.BlockCopy(wire,8,framed,0,framed.Length);
                if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again)||Sha(body)!=Sha(framed))throw new InvalidOperationException("Opaque settings fixture differs");
                string file=label+".bin";File.WriteAllBytes(Path.Combine(output,file),wire);
                defineFixtures.Add(new Dictionary<string,object>{{"file",file},{"command",command},{"data_bytes",count},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true}});
            }
        }
        byte[] ack=new byte[1025331];U16(ack,0,0x3243);U16(ack,2,0xA203);U16(ack,4,8);U16(ack,6,2);U16(ack,8,0x8100);
        byte[] ackWire=Convert(self,meta,pack,ack,32768,"define_save_ack_pack",checks);
        byte[] ackDecoded=Convert(self,meta,unpack,ackWire,1025331,"define_save_ack_unpack",checks);
        byte[] ackAgain=Convert(self,meta,pack,ackDecoded,32768,"define_save_ack_repack",checks);
        if(Sha(ack)!=Sha(ackDecoded)||Sha(ackWire)!=Sha(ackAgain))throw new InvalidOperationException("Settings save ack differs");
        File.WriteAllBytes(Path.Combine(output,"player-define-save-ack.bin"),ackWire);
        result["opaque_player_define_fixtures"]=defineFixtures;
        result["player_define_save_ack"]=new Dictionary<string,object>{{"bytes",ackWire.Length},{"sha256",Sha(ackWire)},{"host_equal",true},{"roundtrip_equal",true}};
        // 当前A005的非零CurRoleGID夹具：只验证已有道具引用的位置，不声明真实资产/角色已加载。
        byte[] roleHost=new byte[1025331];U16(roleHost,0,0x3243);U16(roleHost,2,0xA005);U16(roleHost,4,8);U16(roleHost,8,0x8100);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)10001),0,roleHost,10,8);
        byte[] roleName=Encoding.ASCII.GetBytes("NZRoleProbe");Buffer.BlockCopy(roleName,0,roleHost,22,roleName.Length);
        Buffer.BlockCopy(BitConverter.GetBytes((ulong)1001),0,roleHost,104,8);U32(roleHost,170,1);
        IntPtr roleMetaName=Marshal.StringToHGlobalAnsi("ZN2C_NtfPlayerInfo");IntPtr roleMeta;
        try{roleMeta=Slot<FindMeta>(self,0x58)(self,lib,roleMetaName);}finally{Marshal.FreeHGlobal(roleMetaName);}
        if(roleMeta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed role player info schema");
        byte[] roleBodyHost=new byte[1412];Buffer.BlockCopy(roleHost,8,roleBodyHost,0,1412);
        byte[] roleBody=Convert(self,roleMeta,pack,roleBodyHost,32768,"role_info_body_pack",checks);U16(roleHost,6,roleBody.Length);
        byte[] roleWire=Convert(self,meta,pack,roleHost,32768,"role_info_pack",checks);
        byte[] roleDecoded=Convert(self,meta,unpack,roleWire,1025331,"role_info_unpack",checks);
        byte[] roleAgain=Convert(self,meta,pack,roleDecoded,32768,"role_info_repack",checks);
        byte[] roleFramed=new byte[roleWire.Length-8];Buffer.BlockCopy(roleWire,8,roleFramed,0,roleFramed.Length);
        if(Sha(roleHost)!=Sha(roleDecoded)||Sha(roleWire)!=Sha(roleAgain)||Sha(roleBody)!=Sha(roleFramed))throw new InvalidOperationException("Role player info fixture differs");
        File.WriteAllBytes(Path.Combine(output,"role-player-info-refresh.bin"),roleWire);
        result["role_info_refresh_fixture"]=new Dictionary<string,object>{{"bytes",roleWire.Length},{"sha256",Sha(roleWire)},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true},{"role_or_inventory_verified",false}};
        // FF13在当前元数据中只有16位hint，没有Result；零提示为显式研究值。
        byte[] hints=new byte[1025331];U16(hints,0,0x3243);U16(hints,2,0xFF13);U16(hints,4,8);U16(hints,6,2);
        IntPtr hintsName=Marshal.StringToHGlobalAnsi("ZN2C_NtfZoneHints");IntPtr hintsMeta;
        try{hintsMeta=Slot<FindMeta>(self,0x58)(self,lib,hintsName);}finally{Marshal.FreeHGlobal(hintsName);}
        if(hintsMeta==IntPtr.Zero)throw new InvalidOperationException("Missing current ZoneHints schema");
        byte[] hintsBody=Convert(self,hintsMeta,pack,new byte[2],32768,"zone_hints_body_pack",checks);
        byte[] hintsWire=Convert(self,meta,pack,hints,32768,"zone_hints_pack",checks);
        byte[] hintsDecoded=Convert(self,meta,unpack,hintsWire,1025331,"zone_hints_unpack",checks);
        byte[] hintsAgain=Convert(self,meta,pack,hintsDecoded,32768,"zone_hints_repack",checks);
        if(Sha(hints)!=Sha(hintsDecoded)||Sha(hintsWire)!=Sha(hintsAgain)||hintsWire.Length!=10||hintsBody.Length!=2||hintsWire[8]!=hintsBody[0]||hintsWire[9]!=hintsBody[1])throw new InvalidOperationException("ZoneHints fixture differs");
        File.WriteAllBytes(Path.Combine(output,"zone-no-hints.bin"),hintsWire);
        result["zone_no_hints_fixture"]=new Dictionary<string,object>{{"bytes",hintsWire.Length},{"sha256",Sha(hintsWire)},{"host_equal",true},{"roundtrip_equal",true},{"standalone_body_equal",true},{"hint_semantics_verified",false}};
        // 当前自建房间实际触发A355；A356使用MainChnlInfo固定三个IP而非A358的Zone数组。
        // 心跳A004/FF10各只有一个Padding字节。全host往返确保没有套用其他地区结构。
        var lobbyFixtures=new List<object>();
        foreach(int cmd in new int[]{0xA356,0xA004,0xFF10,0xA133}) {
            byte[] host=new byte[1025331];U16(host,0,0x3243);U16(host,2,cmd);U16(host,4,8);
            if(cmd==0xA356) {
                U16(host,8,0x8100);U16(host,10,1);U16(host,12,1);U16(host,14,2);
                byte[] title=Encoding.ASCII.GetBytes("NZ Online");Buffer.BlockCopy(title,0,host,16,title.Length);
                U32(host,48,100);U32(host,52,1);U32(host,56,0x0100007f);U32(host,60,0x0100007f);U32(host,64,0x0100007f);
            }
            if(cmd==0xA133) {
                // 当前ZoneChannelListInfo无Result；首末包和计数后为1406B Zone结构。
                host[8]=1;host[9]=1;U16(host,10,1);int z=12;
                U32(host,z,1);U32(host,z+4,0x0100007f);U16(host,z+136,19001);U32(host,z+138,1);
                int c=z+142;U32(host,c,1);byte[] title=Encoding.ASCII.GetBytes("NZ-1");Buffer.BlockCopy(title,0,host,c+4,title.Length);
                U32(host,c+36,100);U32(host,c+40,1);U32(host,c+52,1);
            }
            string label="lobby_"+cmd.ToString("x");
            byte[] wire=Convert(self,meta,pack,host,32768,label+"_initial_pack",checks);
            U16(host,6,wire.Length-8);
            wire=Convert(self,meta,pack,host,32768,label+"_pack",checks);
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            byte[] again=Convert(self,meta,pack,decoded,32768,label+"_repack",checks);
            if(Sha(host)!=Sha(decoded)||Sha(wire)!=Sha(again))throw new InvalidOperationException("Current lobby fixture differs");
            string file=label+".bin";File.WriteAllBytes(Path.Combine(output,file),wire);
            lobbyFixtures.Add(new Dictionary<string,object>{{"file",file},{"bytes",wire.Length},{"sha256",Sha(wire)},{"host_equal",true},{"roundtrip_equal",true}});
        }
        result["main_channel_and_heartbeat_fixtures"]=lobbyFixtures;
        ValidateFeatureFixtures(self,meta,pack,unpack,output,result,checks);
        result["player_info_scope"]="SERIALIZATION_FIXTURES_ONLY_NO_GAMEPLAY_OR_ROLE_DEFAULTS_VALIDATED";
        result["network_reply_sent"]=false;result["real_account_data_used"]=false;
    }
    // 新功能夹具只来自本次私人目录；完整host、当前DLL线上包和独立Python包必须同时相等。
    static void ValidateFeatureFixtures(IntPtr self,IntPtr meta,ConvertMeta pack,ConvertMeta unpack,string output,Dictionary<string,object> result,List<object> checks) {
        string[] paths=Directory.GetFiles(output,"feature_*.host");
        // 新增频道/房间消息后超过160；逐文件处理仍有256文件及外部30秒Job双重上限。
        if(paths.Length>256)throw new InvalidOperationException("Too many feature fixtures");
        Array.Sort(paths);var fixtures=new List<object>();
        foreach(string path in paths) {
            string label=Path.GetFileNameWithoutExtension(path);
            byte[] host=File.ReadAllBytes(path);
            if(host.Length!=1025331)throw new InvalidOperationException("Invalid feature host length");
            byte[] python=File.ReadAllBytes(Path.Combine(output,label+".python.bin"));
            if(python.Length>65543)throw new InvalidOperationException("Invalid feature wire length");
            byte[] wire=Convert(self,meta,pack,host,131072,label+"_pack",checks);
            // 即使比对失败也保留无账号夹具的实际DLL输出，便于定位版本字段而不猜测成功形状。
            File.WriteAllBytes(Path.Combine(output,label+".dll.bin"),wire);
            if(Sha(python)!=Sha(wire))throw new InvalidOperationException(label+" Python/DLL mismatch");
            byte[] decoded=Convert(self,meta,unpack,wire,1025331,label+"_unpack",checks);
            if(Sha(host)!=Sha(decoded))throw new InvalidOperationException(label+" full host mismatch");
            byte[] again=Convert(self,meta,pack,decoded,131072,label+"_repack",checks);
            if(Sha(wire)!=Sha(again))throw new InvalidOperationException(label+" repack mismatch");
            fixtures.Add(new Dictionary<string,object>{{"label",label},{"bytes",wire.Length},{"sha256",Sha(wire)},{"full_host_equal",true},{"python_equal",true},{"repack_equal",true}});
        }
        result["feature_fixtures"]=fixtures;
    }
    public static Dictionary<string,object> Run(string dll,string tdr,byte[] request,string output,bool verifyResponse,byte[] zoneRequest,bool zoneNotification,bool zoneLogin,bool zoneLoginResponse,bool defineEmptyResponse,bool playerInfoProfile) {
        bool zoneSample=zoneNotification||zoneLogin||defineEmptyResponse;
        var checks=new List<object>();var result=new Dictionary<string,object>{{"kind",zoneSample?"CURRENT_ZONE_NATIVE_TDR_OFFLINE_PROBE":"CURRENT_GEO_NATIVE_TDR_OFFLINE_PROBE"},{"x86",IntPtr.Size==4},{"network_init_called",false},{"game_started",false},{"checks",checks}};
        IntPtr lib=IntPtr.Zero;IntPtr self=IntPtr.Zero;
        IntPtr module=LoadLibraryEx(dll,IntPtr.Zero,0x1100);
        if(module==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        IntPtr factoryAddress=GetProcAddress(module,"Invoke");if(factoryAddress==IntPtr.Zero)throw new InvalidOperationException("No factory");
        Factory factory=(Factory)Marshal.GetDelegateForFunctionPointer(factoryAddress,typeof(Factory));factory(0x58C,out self);
        if(self==IntPtr.Zero)throw new InvalidOperationException("No protocol object");
        try {
            foreach(int[] pair in new int[][]{new int[]{0x50,0x29530},new int[]{0x54,0x29670},new int[]{0x58,0x29560},new int[]{0x5C,0x294C0},new int[]{0x60,0x294E0}}) CheckSlot(module,self,pair[0],pair[1],checks);
            result["module_address"]=module.ToInt64();result["object_address"]=self.ToInt64();result["vtable_rva"]=Marshal.ReadIntPtr(self).ToInt64()-module.ToInt64();
            byte[] bytes=File.ReadAllBytes(tdr);
            using(Guard metadata=new Guard(bytes.Length)) {
                metadata.Put(bytes);int code=Slot<LoadMeta>(self,0x50)(self,out lib,metadata.Data,bytes.Length);metadata.Check();
                checks.Add(new Dictionary<string,object>{{"step","load_metadata"},{"return_code",code},{"canaries",true}});
                if(code!=0||lib==IntPtr.Zero)throw new InvalidOperationException("Metadata load failed");
            }
            string schema=defineEmptyResponse?"C2ZNPkgHead":(zoneSample?"C2ZNPkg":"C2GEOPkg");int hostCapacity=defineEmptyResponse?8:(zoneSample?1025331:26906);
            IntPtr name=Marshal.StringToHGlobalAnsi(schema);IntPtr meta;
            try{meta=Slot<FindMeta>(self,0x58)(self,lib,name);}finally{Marshal.FreeHGlobal(name);}
            if(meta==IntPtr.Zero)throw new InvalidOperationException("Missing fixed package schema");
            result["schema"]=schema;result["host_capacity"]=hostCapacity;result["metadata_bytes"]=bytes.Length;
            result["metadata_address"]=meta.ToInt64();result["library_address"]=lib.ToInt64();
            // D9810+5C 是打包、+60 是解包；+64 是单参数错误文本查询，禁止按转换接口调用。
            ConvertMeta pack=Slot<ConvertMeta>(self,0x5C),unpack=Slot<ConvertMeta>(self,0x60);
            byte[] host=Convert(self,meta,unpack,request,hostCapacity,"captured_request_unpack",checks);
            result["request_host_prefix"]=BitConverter.ToString(host,0,Math.Min(zoneSample?8:16,host.Length));
            byte[] roundtrip=Convert(self,meta,pack,host,32768,"captured_request_pack",checks);
            File.WriteAllBytes(Path.Combine(output,"research-request-unpacked.bin"),host);
            File.WriteAllBytes(Path.Combine(output,"research-request-repacked.bin"),roundtrip);
            bool same=Sha(request)==Sha(roundtrip);result["request_roundtrip_equal"]=same;result["request_sha256"]=Sha(request);
            if(!same)throw new InvalidOperationException("Captured request roundtrip changed");
            if(defineEmptyResponse) {
                // 请求仅包头有元数据；响应仍用当前真实 A205 联合体与完整主机结构独立核对。
                if(host.Length!=8 || Be16(request,0)!=0x3243 || Be16(request,2)!=0xA204 || Be16(request,4)!=8 || Be16(request,6)!=0)throw new InvalidOperationException("Unexpected A204 header");
                result["request_validation_scope"]="C2ZNPkgHead only; no A204 union body schema";
                ValidateDefineEmptyResponse(self,lib,pack,unpack,output,result,checks);
                result["passed"]=true;return result;
            }
            if(zoneNotification) {
                // 1250 是 NtfNetBarKey 通知，当前元数据选择器已核对；仅记录长度，不输出 KeyData。
                if(host.Length!=1025331 || Be16(request,0)!=0x3243 || Be16(request,2)!=0x1250 || Be16(request,4)!=8 || Be16(request,6)!=3)throw new InvalidOperationException("Unexpected Zone notification shape");
                int keyLength=host[8]|(host[9]<<8);
                if(keyLength!=1)throw new InvalidOperationException("Unexpected bounded notification length");
                result["notification"]=new Dictionary<string,object>{{"name","C2ZN_NtfNetBarKey"},{"command","0x1250"},{"key_length",keyLength},{"key_data_published",false},{"login_request",false},{"response_constructed",false}};
                result["passed"]=true;return result;
            }
            if(zoneLogin) {
                // A000请求只有频道/CRC/IP/平台；比对线上字段与x86布局，不输出身份；响应仅受专项开关控制。
                if(host.Length!=1025331 || request.Length!=26 || Be16(request,0)!=0x3243 || Be16(request,2)!=0xA000 || Be16(request,4)!=8 || Be16(request,6)!=18)throw new InvalidOperationException("Unexpected Zone login shape");
                for(int at=8;at<=20;at+=4)if(BitConverter.ToUInt32(host,at)!=Be32(request,at))throw new InvalidOperationException("Zone login DWORD layout mismatch");
                if(BitConverter.ToUInt16(host,24)!=Be16(request,24))throw new InvalidOperationException("Zone login platform layout mismatch");
                result["login_request"]=new Dictionary<string,object>{{"name","C2ZN_ReqLogin"},{"command","0xA000"},{"body_length",18},{"field_count",5},{"dword_and_u16_layout_verified",true},{"identity_fields_published",false},{"response_constructed",zoneLoginResponse}};
                if(zoneLoginResponse)ValidateLoginResponse(self,lib,meta,pack,unpack,output,result,checks);
                if(playerInfoProfile)ValidatePlayerInfoProfile(self,lib,meta,pack,unpack,output,result,checks);
                result["passed"]=true;return result;
            }
            // 默认先只验收真实研究请求，响应须显式选择并继续单独核对头长度。
            if(!verifyResponse && zoneRequest==null){result["passed"]=true;result["response_validation_requested"]=false;return result;}
            IntPtr bodyName=Marshal.StringToHGlobalAnsi("GEO2C_ResPingList");IntPtr bodyMeta;
            try{bodyMeta=Slot<FindMeta>(self,0x58)(self,lib,bodyName);}finally{Marshal.FreeHGlobal(bodyName);}
            if(bodyMeta==IntPtr.Zero)throw new InvalidOperationException("No GEO2C_ResPingList");
            foreach(bool one in new bool[]{false,true}) {
                string label=one?"loopback":"empty";byte[] responseHost=ResearchResponse(one);
                byte[] bodyHost=new byte[26898];Buffer.BlockCopy(responseHost,8,bodyHost,0,bodyHost.Length);
                // 包头 BodyLen 由当前组件的独立正文序列化结果计算，不假定打包函数替调用方补长度。
                byte[] bodyWire=Convert(self,bodyMeta,pack,bodyHost,32768,label+"_body_pack",checks);
                if(bodyWire.Length>65535)throw new InvalidOperationException("Body too large");
                U16(responseHost,6,bodyWire.Length);
                byte[] response=Convert(self,meta,pack,responseHost,32768,label+"_response_pack",checks);
                if(response.Length<8 || Be16(response,0)!=0x8202 || Be16(response,2)!=0x2001 || Be16(response,4)!=8 || Be16(response,6)!=response.Length-8)throw new InvalidOperationException("Response wire header inconsistent");
                byte[] framedBody=new byte[response.Length-8];Buffer.BlockCopy(response,8,framedBody,0,framedBody.Length);
                if(Sha(bodyWire)!=Sha(framedBody))throw new InvalidOperationException("Standalone body disagrees with framed body");
                byte[] decoded=Convert(self,meta,unpack,response,26906,label+"_response_unpack",checks);
                if(Sha(responseHost)!=Sha(decoded))throw new InvalidOperationException("Decoded host fields disagree with research profile");
                byte[] again=Convert(self,meta,pack,decoded,32768,label+"_response_repack",checks);
                if(Sha(response)!=Sha(again))throw new InvalidOperationException("Response roundtrip changed");
                string filename="ping-response-"+label+".bin";File.WriteAllBytes(Path.Combine(output,filename),response);
                result[label+"_response"]=new Dictionary<string,object>{{"bytes",response.Length},{"body_length",bodyWire.Length},{"sha256",Sha(response)},{"roundtrip_equal",true},{"decoded_host_equal",true},{"header_lengths_equal",true},{"standalone_body_equal",true},{"file",filename}};
            }
            if(zoneRequest!=null)ValidateZone(self,lib,meta,pack,unpack,zoneRequest,output,result,checks);
            result["passed"]=true;return result;
        } catch(Exception error) {
            // 失败也保留已完成的槽位、状态码与长度记录，不把诊断丢给外层简短异常。
            result["passed"]=false;result["error_type"]=error.GetType().FullName;result["message"]=error.Message;return result;
        } finally { if(lib!=IntPtr.Zero) Slot<FreeMeta>(self,0x54)(self,ref lib); }
        // 工厂对象和模块由专用短生命周期进程退出回收，避免未证明的析构虚表调用。
    }
}
'@
try {
    $result = [CurrentGeoTdrProbe]::Run($dll,$tdr,$requestBytes,$outputRoot,$VerifyResearchResponse.IsPresent,$zoneRequestBytes,$VerifyZoneNotification.IsPresent,$zoneLoginMode,$VerifyZoneLoginResponse.IsPresent,$VerifyPlayerDefineEmptyResponse.IsPresent,$VerifyPlayerInfoResearchResponse.IsPresent)
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputRoot 'worker.json') -Encoding UTF8
} catch {
    @{passed=$false;error_type=$_.Exception.GetType().FullName;message=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputRoot 'worker.json') -Encoding UTF8
    throw
}
