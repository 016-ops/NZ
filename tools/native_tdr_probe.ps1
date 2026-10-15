# 当前版本 TDR 的离线 ABI 验证：仅实例化协议组件并调用元数据/序列化接口，不调用网络 Init。
param([Parameter(Mandatory=$true)][string]$OutputDirectory, [switch]$VerifyResearchResponse, [switch]$VerifyZoneResearchResponse, [switch]$VerifyZoneNotification, [switch]$VerifyZoneLogin, [switch]$VerifyZoneLoginResponse, [switch]$VerifyPlayerDefineEmptyResponse)
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
$zoneLoginMode = $VerifyZoneLogin.IsPresent -or $VerifyZoneLoginResponse.IsPresent
$zoneModeCount = [int]($VerifyZoneNotification.IsPresent) + [int]($VerifyZoneLogin.IsPresent) + [int]($VerifyZoneLoginResponse.IsPresent) + [int]($VerifyPlayerDefineEmptyResponse.IsPresent)
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
    public static Dictionary<string,object> Run(string dll,string tdr,byte[] request,string output,bool verifyResponse,byte[] zoneRequest,bool zoneNotification,bool zoneLogin,bool zoneLoginResponse,bool defineEmptyResponse) {
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
    $result = [CurrentGeoTdrProbe]::Run($dll,$tdr,$requestBytes,$outputRoot,$VerifyResearchResponse.IsPresent,$zoneRequestBytes,$VerifyZoneNotification.IsPresent,$zoneLoginMode,$VerifyZoneLoginResponse.IsPresent,$VerifyPlayerDefineEmptyResponse.IsPresent)
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputRoot 'worker.json') -Encoding UTF8
} catch {
    @{passed=$false;error_type=$_.Exception.GetType().FullName;message=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputRoot 'worker.json') -Encoding UTF8
    throw
}
