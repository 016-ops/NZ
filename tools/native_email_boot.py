"""独立邮箱身份原生启动：自有进程、回环采集和配置恢复；手动模式保留到用户退出。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import socket
import select
import struct
import subprocess
import threading
import time
import itertools
from adapters.cn_current.email_ipc import research_mapping, PROXY_SHA256, LOOPBACK_IPC_PROFILE
from adapters.cn_current.transport import TRANSPORT_PROFILE
from tools.client_workspace import digest
from tools.native_recovery import validate
from tools.native_job import JobGroup
from tools.native_probe import loaded_modules

# 普通诊断默认60秒、最多300秒；显式manual-test模式按用户要求保留到退出。
MAX_BOOT_SECONDS = 300


def application_observation(payload):
    """业务观测不持久化房间明文口令或其低熵整包摘要；其余历史观测合同保持。"""
    from adapters.cn_current.zone import parse_zone_header
    header=parse_zone_header(payload)
    private=header['command'] in (0xa10a,0xa103,0xa120)
    result=dict(bytes=len(payload),header={k:header[k] for k in ('magic','command','head_length','body_length')})
    if private:result['body_capture']='REDACTED_ROOM_PASSWORD'
    else:result['sha256']=hashlib.sha256(payload).hexdigest()
    return result,not private


class CapturedFrameStream:
    """为一次帧读取保留至多 64KiB 的私人原始证据，并设置累计期限，拒绝持续慢速分片。"""
    def __init__(self, connection, seconds=5):
        """只包装调用方拥有的连接；不关闭连接，保存数据由探针统一放入私人目录。"""
        self.connection=connection
        self.deadline=time.monotonic()+seconds
        self.content=bytearray()

    def recv(self, count):
        """在总字节和累计时间双重上限内读取；已收头部即使解析失败也留作失败证据。"""
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('FRAME_DEADLINE_EXCEEDED')
        if count<1 or len(self.content)+count>65536:
            raise ValueError('CAPTURE_LIMIT_EXCEEDED')
        self.connection.settimeout(remaining)
        chunk=self.connection.recv(count)
        self.content.extend(chunk)
        return chunk


class SuspendedClient:
    """以当前普通用户创建暂停的已验证工作副本，在主线程执行前绑定 Job 并发布 IPC。"""
    def __init__(self, binary, game_id, directory, output, windowed=False, server_arguments=(), frame_copy=False):
        """仅持有自己刚创建的进程/线程句柄；失败不枚举或控制已有游戏进程。"""
        class Startup(ctypes.Structure):
            """STARTUPINFOW 原生布局，普通窗口显示不需要 UI 自动化。"""
            _fields_=[('cb',wintypes.DWORD),('reserved',wintypes.LPWSTR),('desktop',wintypes.LPWSTR),
                ('title',wintypes.LPWSTR),('x',wintypes.DWORD),('y',wintypes.DWORD),('xs',wintypes.DWORD),
                ('ys',wintypes.DWORD),('xc',wintypes.DWORD),('yc',wintypes.DWORD),('fill',wintypes.DWORD),
                ('flags',wintypes.DWORD),('show',wintypes.WORD),('reserved2',wintypes.WORD),
                ('reserved_ptr',ctypes.c_void_p),('stdin',wintypes.HANDLE),('stdout',wintypes.HANDLE),('stderr',wintypes.HANDLE)]
        class Information(ctypes.Structure):
            """PROCESS_INFORMATION 用于 ResumeThread 和退出查询，不使用远程线程或内存补丁。"""
            _fields_=[('process',wintypes.HANDLE),('thread',wintypes.HANDLE),('pid',wintypes.DWORD),('tid',wintypes.DWORD)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateProcessW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,
            wintypes.BOOL,wintypes.DWORD,ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(Startup),ctypes.POINTER(Information)]
        self.kernel.CreateProcessW.restype=wintypes.BOOL
        self.kernel.ResumeThread.argtypes=[wintypes.HANDLE]
        self.kernel.ResumeThread.restype=wintypes.DWORD
        self.kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        environment=dict(os.environ, __COMPAT_LAYER='RunAsInvoker')
        # 只向自有游戏子进程传递固定私人日志路径，不允许继承任意外部显示诊断路径。
        environment.pop('NZ_FRAME_BRIDGE_LOG', None)
        environment.pop('NZ_FRAME_BRIDGE_DIRECT', None)
        if frame_copy:
            environment['NZ_FRAME_BRIDGE_LOG'] = str(output.resolve()/'frame-bridge.log')
        block=ctypes.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(environment.items(),key=lambda row:row[0].upper()))+'\0\0')
        arguments=[str(binary),*server_arguments,'-q',str(game_id),'-log','-FORCELOGFLUSH','-nomoviestartup',
                   '-ABSLOG='+str(output.resolve()/'engine.log')]
        if windowed:
            # 仅用引擎现有启动参数降低观察窗口大小；不写系统分辨率，也不修改图形代码或认证。
            arguments.extend(['-windowed','-ResX=1280','-ResY=720'])
        command=ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
        info, startup=Information(),Startup()
        startup.cb=ctypes.sizeof(startup)
        if not self.kernel.CreateProcessW(str(binary),command,None,None,False,0x404,block,str(directory),ctypes.byref(startup),ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.pid, self._handle, self.thread=info.pid,info.process,info.thread

    def resume(self):
        """主线程只恢复一次，映射发布和 Job 绑定失败时不会执行客户端代码。"""
        if self.kernel.ResumeThread(self.thread)==0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        """只终止本对象创建且仍持有句柄的进程。"""
        self.kernel.TerminateProcess(self._handle,1)

    def wait(self, timeout=5):
        """限时等待退出，以支持 JobGroup.attach 失败时的清理合同。"""
        if self.kernel.WaitForSingleObject(self._handle,int(timeout*1000))!=0:
            raise TimeoutError('Owned process did not stop')

    def close(self):
        """进程已归 Job 清理后释放本对象的两个句柄，不操作其他进程。"""
        self.kernel.CloseHandle(self.thread)
        self.kernel.CloseHandle(self._handle)


def graphics_preflight(output):
    """只读查询同一上下文的x86 D3D9能力；返回True只证明HAL接口存在，不证明窗口呈现。

    独立诊断已经复现能力、回缓冲及Present成功但窗口空白，因此不得将此处当成画面通过。
    """
    powershell=Path(os.environ['SystemRoot'])/'SysWOW64/WindowsPowerShell/v1.0/powershell.exe'
    environment=dict(os.environ,PSModulePath=str(powershell.parent/'Modules'))
    destination=(output/'d3d9-preflight.json').resolve()
    with JobGroup() as group:
        process=subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-File',
            str(Path(__file__).with_name('native_d3d_caps.ps1')),'-OutputPath',str(destination)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW,env=environment)
        group.attach(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            group.terminate(); process.communicate(timeout=5)
            return False
        if process.returncode or not destination.is_file():
            return False
    result=json.loads(destination.read_text(encoding='utf-8-sig'))
    # 当前游戏使用主适配器的硬件路径。REF 成功不能替代实际默认 HAL 能力。
    return bool(result.get('ProcessX86') and not result.get('Error') and any(
        adapter.get('Index')==0 and any(caps.get('Type')=='HAL' and caps.get('Success')
            and caps.get('PixelShaderMajor',0)>=2 for caps in adapter.get('Capabilities',[]))
        for adapter in result.get('Adapters',[])))


def current_server_map_arguments(root, map_name):
    """从固定当前配置和实际文件选取传统server入口；不采用PH地址或修改客户端机器码。

    地图必须是当前逻辑清单中的精确包名，模式由同配置的精确前缀映射唯一取得；
    本函数只确认入口参数及主资源存在，不证明地图闭包、服务器模式或监听网络成功。
    """
    import re
    from tools.client_catalog import decode_resource, parse_ini, fields
    from tools.native_ui_config import GAME_CONFIG_SHA256
    root=Path(root).resolve(strict=True)
    if not isinstance(map_name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',map_name):
        raise ValueError('INVALID_CURRENT_SERVER_MAP_NAME')
    config=root/'TGame/Config/DefaultGame.ini'
    if digest(config)!=GAME_CONFIG_SHA256:
        raise ValueError('CURRENT_SERVER_MAP_CONFIG_MISMATCH')
    text,_=decode_resource(config.read_bytes())
    records=parse_ini(text,'TGame/Config/DefaultGame.ini')
    logical=[fields(row.get('value')) for row in records if row.get('key')=='GameMapSettings']
    if sum(row.get('MapName')==map_name for row in logical)!=1:
        raise ValueError('CURRENT_SERVER_MAP_NOT_UNIQUE_IN_CONFIG')
    prefix=map_name.split('-',1)[0]
    mappings=[fields(row.get('value')) for row in records if row.get('key')=='DefaultMapPrefixes']
    modes={row.get('GameType') for row in mappings if row.get('Prefix')==prefix}
    if len(modes)!=1:
        raise ValueError('CURRENT_SERVER_MAP_MODE_AMBIGUOUS')
    mode=next(iter(modes))
    if not isinstance(mode,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*',mode):
        raise ValueError('CURRENT_SERVER_MAP_MODE_INVALID')
    matches=list((root/'TGame/CookedPC').rglob(map_name+'.udk'))
    if len(matches)!=1 or matches[0].stat().st_size<125:
        raise ValueError('CURRENT_SERVER_MAP_RESOURCE_MISSING_OR_AMBIGUOUS')
    return ('server',map_name+'?game='+mode,'-unattended','-nosplash')


def probe(root, game_id, game_token, output, seconds=60, sample_threads=False, redirect_game_network=False,
          discover_login_references=False, verify_native_challenge=False, capture_application=False, serve_geo_ping=False,
          serve_geo_zone=False, capture_zone_sequence=False, serve_zone_login=False, serve_player_define=False,
          player_info_nickname=None, player_props_empty=False, windowed=False, role_resource_probe=False,
          role_equip_probe=False, serve_detailed_channel=False, native_settings=None, refresh_role_probe=False,
          disable_screen_texture=False, serve_zone_hints=False, server_map=None, defer_player_info_after_version=False,
          continuous_zone=False, native_inventory=None, manual_test=False, stop_requested=None, progress=None,
          record_packets=True, inventory_send_interval=0, graphics_compat=False, startup_timeout=None, frame_copy=False):
    """核对当前样本并运行自有会话；Launcher 可请求停止并读取阶段，协议连通不等于大厅就绪。"""
    inventory_delivered = threading.Event()

    def notify(stage):
        """只发布固定阶段名，不向启动器界面传递账号、认证帧或任意异常正文。"""
        if stage == 'INVENTORY_SENT':
            inventory_delivered.set()
        if progress is not None:
            progress(stage)

    notify('CLIENT_CHECK')
    if type(graphics_compat) is not bool or type(frame_copy) is not bool or (graphics_compat and frame_copy):
        raise ValueError('INVALID_GRAPHICS_COMPAT_MODE')
    if startup_timeout is not None and (type(startup_timeout) is not int or not 30 <= startup_timeout <= 300):
        raise ValueError('INVALID_STARTUP_TIMEOUT')
    if type(inventory_send_interval) not in (int, float) or not 0 <= inventory_send_interval <= 0.02:
        raise ValueError('INVALID_INVENTORY_SEND_INTERVAL')
    if type(seconds) is not int or not 1 <= seconds <= MAX_BOOT_SECONDS:
        raise ValueError('Native boot duration must be within 1..300 seconds')
    if manual_test and (not continuous_zone or native_inventory is None):
        raise ValueError('Manual test requires continuous Zone and authenticated native inventory')
    if capture_application and not verify_native_challenge:
        raise ValueError('Application capture requires verified native challenge mode')
    if serve_geo_ping and not capture_application:
        raise ValueError('GEO research response requires authenticated application capture')
    if serve_geo_zone and not serve_geo_ping:
        raise ValueError('Zone directory response requires the current PingList exchange')
    if capture_zone_sequence and not serve_geo_zone:
        raise ValueError('Zone sequence capture requires the owned single Zone directory')
    if serve_zone_login and not capture_zone_sequence:
        raise ValueError('Zone login research response requires bounded Zone capture')
    if serve_player_define and not serve_zone_login:
        raise ValueError('Empty player define response requires the verified research login exchange')
    if player_info_nickname is not None and not serve_player_define:
        raise ValueError('Player info research requires the verified player define exchange')
    if player_props_empty and player_info_nickname is None:
        raise ValueError('Empty props research requires the current player info exchange')
    if role_resource_probe and not player_props_empty:
        raise ValueError('Role resource probe requires the fresh empty inventory research chain')
    if role_equip_probe and not role_resource_probe:
        raise ValueError('Role equip probe requires the isolated role resource fixture')
    if serve_detailed_channel and not player_props_empty:
        raise ValueError('Detailed channel research requires the verified player props chain')
    if native_settings is not None and not serve_player_define:
        raise ValueError('Persistent native settings require the verified player define exchange')
    if refresh_role_probe and not role_equip_probe:
        raise ValueError('Role info refresh requires the isolated equipment probe')
    if serve_zone_hints and not refresh_role_probe and native_inventory is None:
        raise ValueError('Zone hints research requires the isolated post-equipment player info refresh')
    if native_inventory is not None and (role_resource_probe or not player_props_empty):
        raise ValueError('Native inventory requires player props and excludes temporary role fixtures')
    if defer_player_info_after_version and player_info_nickname is None:
        raise ValueError('Deferred player info requires the verified player info chain')
    if continuous_zone and not serve_zone_login:
        raise ValueError('Continuous zone observation requires the verified zone login chain')
    player_info_response=None
    if player_info_nickname is not None:
        from adapters.cn_current.zone import build_research_player_info, ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE
        player_info_response=build_research_player_info(game_id=game_id,nickname_bytes=player_info_nickname,
                                                       profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
    root=root.resolve(strict=True)
    metadata,_=validate(root)
    server_arguments=current_server_map_arguments(root,server_map) if server_map is not None else ()
    role_props=None
    if role_resource_probe:
        from tools.native_role_resources import build_role_resource_probe
        role_props=build_role_resource_probe(root,game_id)
    role_info=None
    if refresh_role_probe:
        from tools.native_role_resources import build_role_info_refresh, ROLE_INFO_REFRESH_PROFILE
        role_info=build_role_info_refresh(game_id,player_info_nickname,profile=ROLE_INFO_REFRESH_PROFILE)
    if output.exists() or not output.resolve().is_relative_to(Path(__file__).resolve().parents[1]/'evidence/private'):
        raise ValueError('Native boot evidence requires a new private output directory')
    expected={'TGame.exe':'b81bb4e1256ad1fca1ecbbf93b35979d96c36ca16f8bbfc8d126e9a452df51f8',
              'TGameBase.dll':'79894ee3b24ad12610c23a385f66820ec4bc6ab9049c57ff5867bcdf1462b6ca',
              'GameLoader.exe':'c86c1f63ff24f33da42e7ef358150f0abef68954d2b137df3b5b4c958edd10d1',
              'ProtocalHandler.dll':'e28a7da8e6668feb65f5573819714738bce7210b79c49dfe287a63f2be3da228',
              'TenProxy.dll':PROXY_SHA256}
    binary=root/'Binaries/Win32/TGame.exe'
    if any(digest(binary.parent/name)!=sha for name,sha in expected.items()):
        raise ValueError('Current sample fingerprint or bounded duration mismatch')
    if capture_application and digest(root/'TGame/TDR/proto_c2geo.tdr')!='6d75e846532bb506a858323b8a1c91fa67d3ed0c8766f1d1682f9a8d8afbc8e1':
        raise ValueError('Current GEO metadata fingerprint mismatch')
    if capture_zone_sequence and digest(root/'TGame/TDR/proto_c2zn.tdr')!='c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba':
        raise ValueError('Current Zone metadata fingerprint mismatch')
    output.mkdir(parents=True)
    if not graphics_preflight(output):
        report={'kind':'CURRENT_CLIENT_EMAIL_BOOT_RESEARCH','native_launch_ready':False,
                'game_process_started':False,'role_verified':False,'lobby_verified':False,
                'blocker':'D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT','original_client_writes':0,
                'configuration_restored':True,'cleanup_verified':True}
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    notify('GRAPHICS_CHECKED')
    before={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    from tools.native_client_logs import snapshot_logs
    log_baseline=snapshot_logs(root,before)
    listener=socket.socket()
    listener.bind(('127.0.0.1',0)); listener.listen(4); listener.settimeout(0.1)
    port=listener.getsockname()[1]
    data=research_mapping(game_id,game_token,loopback_port=port,client_path=str(root))
    from tools.native_email_probe import probe as proxy_probe
    # 主游戏运行前，真实当前 DLL 必须能读取同一目录项；把布局错误拦在独立自测进程中。
    verified=proxy_probe(root,game_id,game_token,loopback_port=port,client_path=str(root))
    (output/'proxy-preflight.json').write_text(json.dumps(verified,indent=2),encoding='utf-8')
    if not verified.get('ipc_read_verified'):
        listener.close()
        raise ValueError('Current proxy rejected local server record')
    received=[]; transport_sessions=[]; stop=threading.Event()
    active_connection=[None]; connection_lock=threading.Lock(); pending_connections=[]
    def save_packet(payload, direction):
        """原生帧只留私人证据；公开记录只有长度、摘要和方向，不回显令牌或认证内容。"""
        number=len(received)
        if manual_test and number>=4096:
            return  # 手动测试继续服务，仅停止扩大私人帧证据，不因采集上限断线。
        # Launcher 正常运行不落盘认证原始帧；显式诊断入口保留既有私人证据选项。
        if record_packets:
            (output/f'loopback-{number:02d}.bin').write_bytes(payload)
        received.append({'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'direction':direction})

    def capture():
        """只监听本次回环端口；默认保留首包，显式研究模式才按当前 DLL 合同执行一次挑战校验。"""
        while not stop.is_set():
            try:
                with connection_lock:
                    queued=pending_connections.pop(0) if pending_connections else None
                connection,address=queued if queued is not None else listener.accept()
            except socket.timeout: continue
            except OSError: break
            with connection:
                with connection_lock:
                    if stop.is_set():
                        return
                    active_connection[0]=connection
                if len(transport_sessions)>=16:
                    continue
                connection.settimeout(2)
                session={'auth_verified':False,'challenge_verified':False,'stage':'await_auth'}
                transport_sessions.append(session)
                if not verify_native_challenge:
                    try: payload=connection.recv(65536)
                    except OSError: payload=b''
                    save_packet(payload,'client_to_probe')
                    continue
                reader=None
                try:
                    from adapters.cn_current.transport import TransportError, receive_frame, validate_auth, make_challenge, verify_challenge_reply
                    from adapters.cn_current.email_ipc import local_session_id
                    import secrets
                    # 客户端先建立TCP，再完成图形/资源初始化才发认证。原5秒窗口会在
                    # 首字节到达前关闭慢启动连接；初次认证给30秒累计预算，后续帧仍5秒。
                    # 这只调整等待期限，不放宽身份/随机挑战验证，也不重放任何业务操作。
                    auth_started=time.monotonic()
                    reader=CapturedFrameStream(connection,seconds=30)
                    request=receive_frame(reader); save_packet(request,'client_to_probe'); reader=None
                    session['auth_wait_seconds']=round(time.monotonic()-auth_started,3)
                    session['auth_summary']=validate_auth(request,game_id,game_token)
                    session['auth_verified']=True
                    # 服务端仅持有自己发出的独立令牌和它的域隔离派生 key，不读取游戏进程密钥。
                    key=local_session_id(game_token); challenge=secrets.token_bytes(16)
                    response=make_challenge(key,challenge)
                    session['stage']='send_challenge'
                    connection.sendall(response); save_packet(response,'probe_to_client')
                    session['stage']='await_challenge_reply'
                    reader=CapturedFrameStream(connection)
                    reply=receive_frame(reader); save_packet(reply,'client_to_probe'); reader=None
                    session['challenge_verified']=verify_challenge_reply(reply,key,challenge)
                    session['stage']='challenge_checked'
                    if capture_application:
                        if not session['challenge_verified']:
                            raise TransportError('CHALLENGE_REPLY_MISMATCH')
                        from adapters.cn_current.transport import make_key_change, receive_application_frame, decode_application
                        # 只有身份和随机挑战均通过后，才发送本机生成的新 key；不产生任何角色/大厅响应。
                        new_key=secrets.token_bytes(16)
                        change=make_key_change(key,new_key)
                        session['stage']='send_key_change'
                        connection.sendall(change); save_packet(change,'probe_to_client')
                        session['key_change_sent']=True
                        session['stage']='await_application'
                        reader=CapturedFrameStream(connection,seconds=15)
                        application_frame=receive_application_frame(reader)
                        save_packet(application_frame,'client_to_probe'); reader=None
                        application=decode_application(application_frame,new_key)
                        number=len(transport_sessions)-1
                        (output/f'application-{number:02d}.bin').write_bytes(application)
                        session['application_bytes']=len(application)
                        session['application_sha256']=hashlib.sha256(application).hexdigest()
                        session['application_frame_decrypted']=True
                        session['new_key_application_received']=bool(application)
                        if len(application)>=8:
                            first_magic,first_command,first_head,first_body=struct.unpack('>HHHH',application[:8])
                            if first_head==8 and len(application)==first_head+first_body:
                                session['application_header']={'magic':first_magic,'command':first_command,
                                    'head_length':first_head,'body_length':first_body}
                        from adapters.cn_current.geo import parse_ping_request, GeoProtocolError
                        try:
                            session['application_structure']=parse_ping_request(application)
                            session['geo_ping_request_verified']=True
                        except GeoProtocolError:
                            session['geo_ping_request_verified']=False
                        session['stage']='application_captured'
                        if capture_zone_sequence and session.get('application_header',{}).get('magic')==0x3243:
                            from adapters.cn_current.zone import parse_netbar_key_notification, parse_zone_header, parse_login_request
                            # 只有当前已证通知进入有限 Zone 流程，防止凭魔数把其他未知首包当已就绪会话。
                            session['zone_first_structure']=parse_netbar_key_notification(application)
                            # 首包已证是 NetBarKey 通知；不能把它当作登录或立刻关闭连接而丢掉后续请求。
                            # 至多八条额外应用帧共用十五秒期限；仅显式开关可回复已验证登录请求。
                            session['zone_following_applications']=[]
                            session['zone_sequence_stop_reason']='FRAME_LIMIT'
                            # 持续模式把已实现消息的处理覆盖到整个自有进程观察期，避免第64帧后
                            # 只持有socket却不消费后续大厅请求。时间、帧数和单帧字节仍有界；默认不变。
                            sequence_deadline=time.monotonic()+(seconds+5 if continuous_zone else 15)
                            # A005/A006会引出更多资料请求；显式资料/末包模式分别至16/32帧，
                            # 总时限仍15秒，默认被动模式维持8帧，未知正文不解释/回复。
                            frame_limit=64 if role_equip_probe or serve_detailed_channel else 32 if player_props_empty else 16 if player_info_response is not None else 8
                            if continuous_zone:
                                frame_limit=2048
                            session['continuous_zone_observation']=continuous_zone
                            session['zone_sequence_frame_limit']=frame_limit
                            for sequence in (itertools.count(1) if manual_test else range(1,frame_limit+1)):
                                if stop.is_set() or (not manual_test and time.monotonic()>=sequence_deadline):
                                    session['zone_sequence_stop_reason']='PROBE_STOPPED' if stop.is_set() else 'TIME_LIMIT'
                                    break
                                session['stage']='await_zone_application_sequence'
                                reader=CapturedFrameStream(connection,seconds=30 if manual_test else sequence_deadline-time.monotonic())
                                try:
                                    extra_frame=receive_application_frame(reader)
                                except TimeoutError:
                                    # 本模式故意不发送登录响应；无新字节的等待结束不是客户端协议失败。
                                    # 部分帧超时仍交给外层保存前缀和错误，不能把截断数据掩盖为正常停止。
                                    if reader.content:
                                        raise
                                    reader=None
                                    if manual_test:
                                        continue  # 空闲不结束手动会话；截断帧仍按上面的错误分支处理。
                                    session['zone_sequence_stop_reason']='IDLE_TIME_LIMIT' if session.get('zone_login_response_sent') else 'IDLE_TIME_LIMIT_NO_RESPONSE_SENT'
                                    break
                                save_packet(extra_frame,'client_to_probe'); reader=None
                                extra=decode_application(extra_frame,new_key)
                                extra_summary,capturable=application_observation(extra)
                                if capturable and (not manual_test or sequence<=2048):
                                    (output/f'application-{number:02d}-sequence-{sequence:02d}.bin').write_bytes(extra)
                                header=parse_zone_header(extra)
                                if header['command']==0xA000:
                                    extra_summary['structure']=parse_login_request(extra)
                                    session['zone_login_request_verified']=True
                                if not manual_test or len(session['zone_following_applications'])<2048:
                                    session['zone_following_applications'].append(extra_summary)
                                session['zone_messages_processed']=sequence
                                if continuous_zone and header['command']==0xA004:
                                    from adapters.cn_current.zone import build_current_heartbeat_response
                                    from adapters.cn_current.transport import make_application_response
                                    heartbeat=build_current_heartbeat_response(extra)
                                    frame=make_application_response(heartbeat,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    session['heartbeat_responses']=session.get('heartbeat_responses',0)+1
                                    features=getattr(native_inventory,'features',None)
                                    if features is not None and session.get('business_inventory_projected') and not select.select([connection],[],[],0)[0]:
                                        # 有已排队的原生操作时先回应操作；合并会话读只在空闲心跳补取。
                                        for response in features.poll():
                                            frame=make_application_response(response,new_key)
                                            connection.sendall(frame);save_packet(frame,'probe_to_client')
                                        features.delivered()
                                if serve_detailed_channel and header['command']==0xA355:
                                    from adapters.cn_current.zone import build_research_main_channel, ZONE_DETAILED_CHANNEL_PROFILE
                                    from adapters.cn_current.transport import make_application_response
                                    if extra[8:]!=bytes(4):
                                        raise TransportError('UNVERIFIED_MAIN_CHANNEL_PADDING')
                                    response=build_research_main_channel(profile=ZONE_DETAILED_CHANNEL_PROFILE)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    (output/f'main-channel-response-{number:02d}-{sequence:02d}.bin').write_bytes(response)
                                    session['main_channel_responses']=session.get('main_channel_responses',0)+1
                                if serve_detailed_channel and header['command']==0xA132:
                                    from adapters.cn_current.zone import build_research_zone_channels, ZONE_DETAILED_CHANNEL_PROFILE
                                    response=build_research_zone_channels(extra,port=port,profile=ZONE_DETAILED_CHANNEL_PROFILE)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    session['zone_channel_responses']=session.get('zone_channel_responses',0)+1
                                if native_settings is not None and header['command']==0xA202:
                                    if not session.get('player_define_response_sent'):
                                        raise TransportError('SET_PLAYER_DEFINE_BEFORE_GET')
                                    from adapters.cn_current.zone import (parse_set_player_define_request,
                                        build_set_player_define_committed_response, ZONE_OPAQUE_PLAYER_DEFINE_PROFILE)
                                    from adapters.cn_current.transport import make_application_response
                                    # API失败/超时/版本冲突抛出即停止，不发送虚假成功；提交摘要不含设置正文。
                                    committed=native_settings.save(parse_set_player_define_request(extra))
                                    response=build_set_player_define_committed_response(profile=ZONE_OPAQUE_PLAYER_DEFINE_PROFILE)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    (output/f'player-define-save-response-{number:02d}-{sequence:02d}.bin').write_bytes(response)
                                    session['native_settings_committed']=committed
                                    session['native_settings_save_acks']=session.get('native_settings_save_acks',0)+1
                                if serve_detailed_channel and header['command']==0xA357:
                                    from adapters.cn_current.zone import (parse_detailed_main_channel_request,
                                        build_research_detailed_main_channel, ZONE_DETAILED_CHANNEL_PROFILE)
                                    from adapters.cn_current.transport import make_application_response
                                    if parse_detailed_main_channel_request(extra)!=0:
                                        raise TransportError('UNVERIFIED_DETAILED_CHANNEL_PADDING')
                                    # 当前DLL61B完整往返已证；一区服一频道指向本次监听器。
                                    # Result取当前ZONE_ERR_SUCC声明，实际消费者推进仍单独记录，不冒称大厅。
                                    response=build_research_detailed_main_channel(port=port,profile=ZONE_DETAILED_CHANNEL_PROFILE)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    (output/f'detailed-channel-response-{number:02d}-{sequence:02d}.bin').write_bytes(response)
                                    session['detailed_channel_response_profile']=ZONE_DETAILED_CHANNEL_PROFILE
                                    session['detailed_channel_responses']=session.get('detailed_channel_responses',0)+1
                                if role_equip_probe and header['command']==0xA008:
                                    if not session.get('role_resource_probe_sent'):
                                        raise TransportError('ROLE_EQUIP_BEFORE_RESOURCE_PROBE')
                                    from tools.native_role_resources import build_role_equip_probe_response, ROLE_EQUIP_PROBE_PROFILE
                                    from adapters.cn_current.transport import make_application_response
                                    # 精确绑定已发送的临时角色；不回应其他资产操作，也不回写业务库存。
                                    response=build_role_equip_probe_response(extra,profile=ROLE_EQUIP_PROBE_PROFILE)
                                    response_frame=make_application_response(response,new_key)
                                    connection.sendall(response_frame); save_packet(response_frame,'probe_to_client')
                                    (output/f'role-equip-probe-response-{number:02d}-{sequence:02d}.bin').write_bytes(response)
                                    session['role_equip_probe_responses']=session.get('role_equip_probe_responses',0)+1
                                    session['role_equip_probe_profile']=ROLE_EQUIP_PROBE_PROFILE
                                    if role_info is not None and not session.get('role_info_refresh_sent'):
                                        # 先完成道具与装备，才让A005引用本Job已知的GID；不另授予真实资产。
                                        refresh_frame=make_application_response(role_info,new_key)
                                        connection.sendall(refresh_frame); save_packet(refresh_frame,'probe_to_client')
                                        (output/f'role-player-info-refresh-{number:02d}.bin').write_bytes(role_info)
                                        session['role_info_refresh_sent']=True
                                        session['role_info_refresh_profile']=ROLE_INFO_REFRESH_PROFILE
                                        if serve_zone_hints:
                                            from adapters.cn_current.zone import build_research_no_zone_hints, ZONE_NO_HINTS_PROFILE
                                            # 当前FF13无Result，仅发送显式零hint；不把旧版顺序当作当前大厅已就绪。
                                            hints=build_research_no_zone_hints(profile=ZONE_NO_HINTS_PROFILE)
                                            hints_frame=make_application_response(hints,new_key)
                                            connection.sendall(hints_frame); save_packet(hints_frame,'probe_to_client')
                                            (output/f'zone-no-hints-{number:02d}.bin').write_bytes(hints)
                                            session['zone_no_hints_sent']=True
                                            session['zone_no_hints_profile']=ZONE_NO_HINTS_PROFILE
                                if native_inventory is not None and header['command']==0xA50E:
                                    response=native_inventory.tp_balance_response(extra)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    session['native_tp_balance_responses']=session.get('native_tp_balance_responses',0)+1
                                features=getattr(native_inventory,'features',None)
                                if features is not None and header['command'] in features.COMMANDS:
                                    if not session.get('business_inventory_projected'):
                                        raise TransportError('FEATURE_BEFORE_INVENTORY')
                                    for response in features.handle(extra):
                                        frame=make_application_response(response,new_key)
                                        connection.sendall(frame);save_packet(frame,'probe_to_client')
                                    # 全部帧发送成功才记录传输确认；断线和不确定提交由持久回执恢复。
                                    features.delivered()
                                    session['feature_requests_processed']=session.get('feature_requests_processed',0)+1
                                if native_inventory is not None and header['command']==0xA008:
                                    if not session.get('business_inventory_projected'):
                                        raise TransportError('NATIVE_EQUIP_BEFORE_INVENTORY')
                                    before_info=native_inventory.player_info(player_info_nickname)
                                    from tools.native_inventory_bridge import NativeEquipmentUnsupported, equipment_failure_response
                                    try:
                                        response=native_inventory.equip(extra)
                                    except NativeEquipmentUnsupported as error:
                                        # 未支持装备是操作失败，不是会话失败；发送明确负应答后继续收心跳。
                                        # 不吞认证/数据库/回读故障，不发送A00B，也不累计成功换装。
                                        response=equipment_failure_response(extra,error)
                                        frame=make_application_response(response,new_key)
                                        connection.sendall(frame); save_packet(frame,'probe_to_client')
                                        rejected=session.setdefault('native_equipment_rejections',[])
                                        if len(rejected)<256:
                                            rejected.append(dict(result=error.result_code))
                                            (output/f'equipment-rejected-{number:02d}-{len(rejected):03d}.bin').write_bytes(response)
                                        continue
                                    timings=session.setdefault('native_equipment_timings',[])
                                    if len(timings)<2048:
                                        timings.append(native_inventory.last_equip_timing)
                                    frame=make_application_response(response,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    notification=native_inventory.last_notification
                                    frame=make_application_response(notification,new_key)
                                    connection.sendall(frame); save_packet(frame,'probe_to_client')
                                    # 新增默认刀先公布真实实例，再通知回退挂接；既有刀也明确刷新空出的背包槽。
                                    for notification in native_inventory.last_asset_notifications+native_inventory.last_default_notifications:
                                        frame=make_application_response(notification,new_key)
                                        connection.sendall(frame);save_packet(frame,'probe_to_client')
                                    session['native_equipment_acknowledgements']=session.get('native_equipment_acknowledgements',0)+1
                                    session['native_equipment_changes_committed']=session.get('native_equipment_changes_committed',0)+int(native_inventory.last_commit_status=='SAVED')
                                    refresh=native_inventory.player_info(player_info_nickname)
                                    # 重复完整A005会再次触发客户端整理未装备物品；只有角色GID实际改变才刷新。
                                    if refresh != before_info:
                                        frame=make_application_response(refresh,new_key)
                                        connection.sendall(frame); save_packet(frame,'probe_to_client')
                                if serve_zone_login and header['command']==0xA000:
                                    if session.get('zone_login_response_sent'):
                                        raise TransportError('DUPLICATE_ZONE_LOGIN_REQUEST')
                                    from adapters.cn_current.zone import build_research_login_response, ZONE_LOGIN_RESPONSE_PROFILE
                                    from adapters.cn_current.transport import make_application_response
                                    # 此固定响应仅验证当前消费者分支，无库存赠送、钱包映射、建角或大厅成功声明。
                                    login_response=build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE)
                                    if native_inventory is not None:
                                        from adapters.cn_current.zone import build_clock_login_response, ZONE_CLOCK_COMPAT_PROFILE
                                        # 研究零时刻使全部当前商品仍未到上架日；使用实际时间兼容合同，不改系统时间/资源日期。
                                        login_response=native_inventory.login_response(int(time.time()))
                                        session['server_clock_profile']=ZONE_CLOCK_COMPAT_PROFILE
                                    session['stage']='send_research_zone_login_response'
                                    login_frame=make_application_response(login_response,new_key)
                                    connection.sendall(login_frame)
                                    # 保存实际已发帧一次，不重新加密生成不同随机填充的伪证据。
                                    save_packet(login_frame,'probe_to_client')
                                    (output/f'zone-login-response-{number:02d}.bin').write_bytes(login_response)
                                    session['zone_login_response_profile']=ZONE_LOGIN_RESPONSE_PROFILE
                                    session['zone_login_response_sent']=True
                                    session['zone_login_response_bytes']=len(login_response)
                                    session['zone_login_response_sha256']=hashlib.sha256(login_response).hexdigest()
                                    notify('ZONE_CONNECTED')
                                if defer_player_info_after_version and header['command']==0xFF05:
                                    if header['body_length']!=4:
                                        raise TransportError('UNEXPECTED_CURRENT_VERSION_NOTIFICATION_LENGTH')
                                    # 仅记录已接收的四字节版本通知，不解释其安全语义，不复制或改写正文。
                                    session['post_login_version_observed']=True
                                if serve_player_define and header['command']==0xA204:
                                    if not session.get('zone_login_response_sent') or session.get('player_define_response_sent'):
                                        raise TransportError('UNEXPECTED_PLAYER_DEFINE_REQUEST_STATE')
                                    from adapters.cn_current.zone import (parse_player_define_request,
                                        build_research_empty_player_define_response, ZONE_EMPTY_PLAYER_DEFINE_PROFILE)
                                    extra_summary['structure']=parse_player_define_request(extra)
                                    # 空资料只走当前已证的零长度完成分支；不覆盖真实玩家自定义数据或生成未知版本内容。
                                    define_profile=ZONE_EMPTY_PLAYER_DEFINE_PROFILE
                                    if native_settings is not None:
                                        from adapters.cn_current.zone import build_player_define_data_response, ZONE_OPAQUE_PLAYER_DEFINE_PROFILE
                                        define_profile=ZONE_OPAQUE_PLAYER_DEFINE_PROFILE
                                        define_response=build_player_define_data_response(native_settings.data,profile=define_profile)
                                        session['native_settings_loaded_revision']=native_settings.revision
                                        session['native_settings_loaded_bytes']=len(native_settings.data)
                                    else:
                                        define_response=build_research_empty_player_define_response(profile=define_profile)
                                    define_frame=make_application_response(define_response,new_key)
                                    connection.sendall(define_frame); save_packet(define_frame,'probe_to_client')
                                    (output/f'player-define-response-{number:02d}.bin').write_bytes(define_response)
                                    session['player_define_response_profile']=define_profile
                                    session['player_define_response_sent']=True
                                    session['player_define_response_bytes']=len(define_response)
                                    session['player_define_response_sha256']=hashlib.sha256(define_response).hexdigest()
                                if (player_info_response is not None and session.get('player_define_response_sent')
                                        and not session.get('player_info_response_sent')
                                        and (not defer_player_info_after_version or session.get('post_login_version_observed'))):
                                    if defer_player_info_after_version:
                                        # 单独检验发送时序：只等待已观察通知后的一秒，不构造或回应反作弊数据。
                                        # 这不是当前原生门禁结论；默认不改变原顺序，总采集期限仍保持15秒。
                                        if stop.wait(1):
                                            break
                                        session['player_info_publish_trigger']='A205_AND_OBSERVED_FF05_PLUS_ONE_SECOND_DIAGNOSTIC'
                                    else:
                                        session['player_info_publish_trigger']='A205'
                                    # 当前编码已由代码/系统导出核对，正文仍是显式空资产研究配置；
                                    # 只使用fresh me中的原昵称和绑定game_id，不授予/清空数据库资产。
                                    info_frame=make_application_response(player_info_response,new_key)
                                    connection.sendall(info_frame); save_packet(info_frame,'probe_to_client')
                                    (output/f'player-info-response-{number:02d}.bin').write_bytes(player_info_response)
                                    session['player_info_response_sent']=True
                                    session['player_info_response_profile']=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE
                                    session['player_info_response_bytes']=len(player_info_response)
                                    session['player_info_response_sha256']=hashlib.sha256(player_info_response).hexdigest()
                                    session['player_info_nickname_code_page']=936
                                    session['player_info_real_assets_projected']=False
                                    if native_inventory is not None:
                                        # 快照来自真实API并已核对稳定GID；只发送已核验类型，末包后引用真实角色。
                                        packets=native_inventory.packets()
                                        for props in packets:
                                            frame=make_application_response(props,new_key)
                                            connection.sendall(frame); save_packet(frame,'probe_to_client')
                                            # 仅显式诊断时控制登录快照发送间隔，比较客户端队列的时序行为；默认不变。
                                            if inventory_send_interval and stop.wait(inventory_send_interval):
                                                break
                                        session['business_inventory_projected']=True
                                        session['native_inventory_owned_count']=len(native_inventory.items)
                                        session['native_inventory_projected_count']=len(native_inventory.items) if native_inventory.paged else sum(x['consume_type'] in (0,2) for x in native_inventory.items)
                                        session['native_inventory_packet_count']=len(packets)
                                        notify('INVENTORY_SENT')
                                        features=getattr(native_inventory,'features',None)
                                        if features is not None:
                                            for response in [features.skin_snapshot(),features.swallow_snapshot(),features.engineering_snapshot(),*features.friend_snapshot(),*features.clan_snapshot()]:
                                                frame=make_application_response(response,new_key)
                                                connection.sendall(frame);save_packet(frame,'probe_to_client')
                                        refresh=native_inventory.player_info(player_info_nickname)
                                        frame=make_application_response(refresh,new_key)
                                        connection.sendall(frame); save_packet(frame,'probe_to_client')
                                        if serve_zone_hints:
                                            from adapters.cn_current.zone import build_research_no_zone_hints, ZONE_NO_HINTS_PROFILE
                                            frame=make_application_response(build_research_no_zone_hints(profile=ZONE_NO_HINTS_PROFILE),new_key)
                                            connection.sendall(frame); save_packet(frame,'probe_to_client')
                                            session['zone_no_hints_sent']=True
                                    elif player_props_empty:
                                        from adapters.cn_current.zone import build_research_empty_player_props, ZONE_EMPTY_PLAYER_PROPS_PROFILE
                                        # 仅fresh me确认为空库存的既有验收角色可走此终包研究分支。
                                        # 当前153D940零计数跳过数组，末包条件在153DE2E置bit1并调用原生完成函数。
                                        props=role_props if role_props is not None else build_research_empty_player_props(profile=ZONE_EMPTY_PLAYER_PROPS_PROFILE)
                                        props_frame=make_application_response(props,new_key)
                                        connection.sendall(props_frame); save_packet(props_frame,'probe_to_client')
                                        label='player-props-role-resource' if role_props is not None else 'player-props-empty'
                                        (output/f'{label}-response-{number:02d}.bin').write_bytes(props)
                                        if role_props is not None:
                                            from tools.native_role_resources import ROLE_RESOURCE_PROBE_PROFILE
                                            session['role_resource_probe_sent']=True
                                            session['role_resource_probe_profile']=ROLE_RESOURCE_PROBE_PROFILE
                                            session['business_inventory_projected']=False
                                        else:
                                            session['player_props_empty_response_sent']=True
                                            session['player_props_empty_response_profile']=ZONE_EMPTY_PLAYER_PROPS_PROFILE
                                            session['player_props_empty_response_sha256']=hashlib.sha256(props).hexdigest()
                            session['stage']='zone_application_sequence_captured'
                            # 有界采集结束不等于服务端踢人；保持本次已认证连接到主探针清理，
                            # 避免人为EOF触发当前尚未实现的重连IDENT，使后续记录混成登录失败。
                            session['connection_held_after_capture']=True
                            stop.wait(None if manual_test else seconds+5)
                        if serve_geo_ping and session['geo_ping_request_verified']:
                            from adapters.cn_current.geo import build_research_ping_response, GEO_PING_RESPONSE_PROFILE
                            from adapters.cn_current.transport import make_application_response, DOWNLINK_PROFILE
                            # 研究目录只含本机一条测速记录；Rank 与延迟取值另行版本化，不能代称原版规则。
                            ping=build_research_ping_response(profile=GEO_PING_RESPONSE_PROFILE)
                            response_frame=make_application_response(ping,new_key)
                            session['geo_response_profile']=GEO_PING_RESPONSE_PROFILE
                            session['downlink_profile']=DOWNLINK_PROFILE
                            session['stage']='send_geo_ping_response'
                            connection.sendall(response_frame); save_packet(response_frame,'probe_to_client')
                            session['geo_ping_response_sent']=True
                            session['stage']='await_next_geo_request'
                            reader=CapturedFrameStream(connection,seconds=15)
                            following_frame=receive_application_frame(reader)
                            save_packet(following_frame,'client_to_probe'); reader=None
                            following=decode_application(following_frame,new_key)
                            (output/f'application-{number:02d}-next.bin').write_bytes(following)
                            session['next_application_bytes']=len(following)
                            session['next_application_sha256']=hashlib.sha256(following).hexdigest()
                            if len(following)>=8:
                                magic,command,head_length,body_length=struct.unpack('>HHHH',following[:8])
                                # 仅公开已证头字段；未知正文仍只在私人目录，绝不根据形状猜角色或账号内容。
                                if magic==0x8202 and head_length==8 and len(following)==head_length+body_length:
                                    session['next_geo_header']={'magic':magic,'command':command,'head_length':head_length,'body_length':body_length}
                                    session['geo_ping_response_advanced_to_zone_list']=command==0x1000
                            session['stage']='next_geo_request_captured'
                            if serve_geo_zone:
                                from adapters.cn_current.geo import parse_zone_request, build_research_zone_response, GEO_ZONE_RESPONSE_PROFILE
                                # 当前 DLL 已互证下一条请求；只向它返回本次监听端口，不能携带任意公网目标。
                                parsed_zone=parse_zone_request(following)
                                session['geo_zone_request_verified']=True
                                session['geo_zone_requested_server_count']=parsed_zone['server_count']
                                directory=build_research_zone_response(port=port,profile=GEO_ZONE_RESPONSE_PROFILE)
                                zone_frame=make_application_response(directory,new_key)
                                connection.sendall(zone_frame); save_packet(zone_frame,'probe_to_client')
                                session['geo_zone_response_profile']=GEO_ZONE_RESPONSE_PROFILE
                                session['geo_zone_response_sent']=True
                                session['stage']='await_zone_tcp_connection'
                                # 暂持 Geo 连接，等客户端打开下一连接后再回到顺序处理；避免发完即关造成 EOF 抢先清队列。
                                next_connection_deadline=time.monotonic()+10
                                while not stop.is_set() and time.monotonic()<next_connection_deadline:
                                    try: accepted=listener.accept()
                                    except socket.timeout: continue
                                    with connection_lock:
                                        if stop.is_set():
                                            accepted[0].close()
                                        else:
                                            pending_connections.append(accepted)
                                            session['next_tcp_connection_observed']=True
                                    break
                                session['stage']='zone_directory_sent'
                except Exception as error:
                    if stop.is_set() and (isinstance(error,OSError) or
                            isinstance(error,TransportError) and str(error)=='TRUNCATED_FRAME'):
                        session['stage']='probe_cleanup_interrupted_receive'
                        session['zone_sequence_stop_reason']='PROBE_STOPPED'
                        session['cleanup_receive_error_type']=type(error).__name__
                        continue
                    session['error_type']=type(error).__name__
                    notify('CONNECTION_ERROR')
                    if isinstance(error,TransportError):
                        session['error_code']=str(error)  # 该模块只抛固定错误码，不拼入输入材料。
                    if reader is not None and reader.content:
                        try: save_packet(bytes(reader.content),'client_to_probe_rejected_prefix')
                        except Exception as evidence_error:
                            session['failure_evidence_error_type']=type(evidence_error).__name__
    worker=threading.Thread(target=capture,daemon=True); worker.start()
    guard=None; group=None
    mappings={}; process=None
    report={'kind':'CURRENT_CLIENT_EMAIL_BOOT_RESEARCH','native_launch_ready':False,
            'role_verified':False,'lobby_verified':False,'loopback_port':port,'qq_credentials_used':False,
            'ipc_mapping_capacity':0x80000,'proxy_preflight_verified':True,'graphics_preflight_verified':True,
            'ipc_profile':LOOPBACK_IPC_PROFILE,
            'transport_research_profile':TRANSPORT_PROFILE if verify_native_challenge else None,
            'original_client_writes':0}
    if capture_application:
        from adapters.cn_current.transport import APPLICATION_PROFILE
        report['application_research_profile']=APPLICATION_PROFILE
    try:
        from tools.native_config_guard import NativeConfigGuard
        guard=NativeConfigGuard(root,output)
        from tools.native_shop_config import install as install_shop_config
        report['shop_compatibility']=install_shop_config(guard)
        # 真实客户端拒绝重压缩UI包；保留失败证据和离线修复器，启动继续使用可加载的原包。
        # 工坊免费按钮日期显示仍待修复，不能把字节级回归视为原生加载通过。
        report['script_compatibility']={'profile':'cn2020-workshop-date-fields-v1',
            'status':'NATIVE_LOAD_REJECTED_ORIGINAL_RETAINED'}
        if graphics_compat:
            from tools.native_graphics_compat import install
            report['graphics_compatibility'] = install(guard)
        elif frame_copy:
            from tools.native_frame_bridge import install
            report['graphics_compatibility'] = install(guard)
        if graphics_compat or frame_copy:
            notify('GRAPHICS_COMPAT_APPLIED')
        if disable_screen_texture:
            from tools.native_ui_config import disable_current_screen_texture, UI_TEXTURE_PROFILE
            variant=disable_current_screen_texture((root/'TGame/Config/DefaultGame.ini').read_bytes())
            guard.apply('TGame/Config/DefaultGame.ini',variant)
            report['ui_screen_texture_diagnostic_profile']=UI_TEXTURE_PROFILE
        if redirect_game_network:
            # 当前样本还有独立的 Geo/Zone 默认配置；研究变体仅定点改四个网络值，不能冒充协议实现。
            from tools.native_network_config import redirect_current_network, PROFILE
            path=root/'TGame/Config/DefaultTGNet.ini'; original=path.read_bytes()
            content=redirect_current_network(original,port)
            (output/'DefaultTGNet.ini.before').write_bytes(original)
            guard.apply('TGame/Config/DefaultTGNet.ini',content)
            report['game_network_research_profile']=PROFILE
        else:
            report['game_network_research_profile']=None
        # 只临时修改独立恢复副本的两个文本配置，并记录前后哈希；原客户端不写入。
        for relative in ('TCLS/config/dirserver.xml','TCLS/config/VersionServerConfig.xml'):
            path=root/relative; original=path.read_bytes()
            (output/(path.name+'.before')).write_bytes(original)
            content=('<Server><DirServer count="1" SuccessfulServerID="1" SuccessfulPort="'+str(port)+'">'
                '<Server1 Catalog="" host="127.0.0.1"/><portlist count="1" port1="'+str(port)+'">0</portlist></DirServer>'
                '<RoleServer count="1" SuccessfulServerID="1" SuccessfulPort="'+str(port)+'"><Server1 Catalog="" host="127.0.0.1"/>'
                '<portlist count="1" port1="'+str(port)+'">0</portlist></RoleServer></Server>').encode()
            if path.name=='VersionServerConfig.xml':
                # 保留实际 XML 的结构，仅将可读节点中的 host 和数值端口替换，不复制 PH 配置。
                import re
                text=original.decode('utf-8-sig')
                text=re.sub(r'host="[^"]+"','host="127.0.0.1"',text)
                text=re.sub(r'(port\d*|SuccessfulPort)="\d+"',lambda m:m.group(1)+'="'+str(port)+'"',text)
                content=text.encode('utf-8')
            guard.apply(relative,content)
        with JobGroup() as group:
            process=SuspendedClient(binary,game_id,binary.parent,output,windowed=windowed,
                server_arguments=server_arguments,frame_copy=frame_copy)
            group.attach(process)
            # 当前 GameLoader 的转交路径复制 0x80000 字节，映射容量不能沿用 DLL 单独自测的 64KiB。
            mapping=mmap.mmap(-1,0x80000,tagname='TCLS_SHAREDMEMEMORY'+str(process.pid),access=mmap.ACCESS_WRITE)
            mapping.write(data); mappings[process.pid]=mapping
            process.resume(); report['game_process_started']=True
            notify('PROCESS_STARTED')
            report['windowed_1280x720_requested']=windowed
            report['engine_server_arguments_requested']=list(server_arguments)
            report['native_server_mode_verified']=False
            started=time.monotonic(); deadline=started+seconds; next_network_sample=started
            report['manual_test_until_user_exit']=manual_test
            report['tcp_samples']=[]
            last_pids=[]
            while manual_test or time.monotonic()<deadline:
                # 停止只影响本次创建并绑定 Job 的进程；不能按进程名称结束其他客户端。
                if stop_requested is not None and stop_requested.is_set():
                    report['launcher_stop_requested']=True
                    break
                # 转交进程可能在游戏初始化崩溃后继续存活。只对首次仓库握手限时，
                # 到期仍走自有Job、持久配置及凭据清理；进入运行后不限制游戏时长。
                if startup_timeout is not None and not inventory_delivered.is_set() and time.monotonic()-started >= startup_timeout:
                    report['startup_error']='CLIENT_STARTUP_TIMEOUT'
                    break
                pids=group.pids()
                if not pids: break
                if pids!=last_pids:
                    (output/'owned-pids.json').write_text(json.dumps(pids),encoding='utf-8')
                    last_pids=pids
                for pid in pids:
                    if pid not in mappings:
                        mapping=mmap.mmap(-1,0x80000,tagname='TCLS_SHAREDMEMEMORY'+str(pid),access=mmap.ACCESS_WRITE)
                        mapping.write(data); mappings[pid]=mapping
                if time.monotonic()>=next_network_sample:
                    from tools.native_tcp_probe import sample_owned_tcp, sample_owned_udp
                    snapshot=sample_owned_tcp(group)
                    snapshot['elapsed_seconds']=round(time.monotonic()-started,3)
                    if not manual_test or len(report['tcp_samples'])<2048:
                        report['tcp_samples'].append(snapshot)
                    if manual_test:
                        # 运行状态不含令牌；结束标记只在用户关闭游戏后更新，不能提前声称已清理。
                        (output/'manual-test-state.json').write_text(json.dumps(dict(state='RUNNING',
                            elapsed_seconds=snapshot['elapsed_seconds'],owned_pids=pids,
                            automatic_time_limit=False,cleanup_pending=True)),encoding='utf-8')
                    if server_arguments:
                        udp=sample_owned_udp(group)
                        udp['elapsed_seconds']=snapshot['elapsed_seconds']
                        report.setdefault('udp_samples',[]).append(udp)
                    next_network_sample=time.monotonic()+2
                time.sleep(0.01)
            report['observed_pids']=sorted(mappings)
            report['loaded_modules']={str(pid):loaded_modules(pid) for pid in group.pids()}
            if sample_threads:
                from tools.native_thread_probe import sample_owned_threads
                report['thread_sample']=sample_owned_threads(group,output,discover_login_references,(game_id,port))
            report['timed_out']=bool(group.pids())
            # 先标记正常限时收尾，再终止自有游戏。持续接收中的socket会随对端退出而复位，
            # 这个停止标记让清理中断与运行期间真实断线分开，不吞掉此前已经发生的错误。
            stop.set()
            if group.pids(): group.terminate()
            process.wait()
            time.sleep(0.1)
            report['cleanup_verified']=not group.pids()
            if manual_test:
                (output/'manual-test-state.json').write_text(json.dumps(dict(state='GAME_EXITED',
                    automatic_time_limit=False,cleanup_pending=True)),encoding='utf-8')
            report['exit_codes']={str(pid):hex(code) for pid,code in group.collect_exits(timeout_ms=1000).items()}
    finally:
        notify('CLEANING')
        # 释放句柄失败也不能跳过持久配置计划的逐项恢复。只记录异常类型，不回显私人材料。
        release_errors=[]
        try:
            if process:
                try: process.close()
                except Exception as error: release_errors.append(type(error).__name__)
            for mapping in mappings.values():
                try: mapping.close()
                except Exception as error: release_errors.append(type(error).__name__)
        finally:
            stop.set(); listener.close()
            # 关闭已经 accept 的连接，立即打断帧读取；只关闭监听器不能停止接收中的 worker。
            with connection_lock:
                if active_connection[0] is not None:
                    try: active_connection[0].shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    active_connection[0].close()
                for pending,_ in pending_connections:
                    pending.close()
                pending_connections.clear()
            worker.join(timeout=3)
            report['transport_capture_stopped']=not worker.is_alive()
            # with块异常也已由JobGroup.close限时等待退出，不能只依赖正常循环尾部才写入的标记。
            # 真正无法确认进程清理时仍为false；独立记录清理错误，不覆盖原来的业务/探针错误。
            report['cleanup_verified']=bool(
                (group.cleanup_verified if group is not None else not report.get('game_process_started'))
                and not worker.is_alive())
            report['process_cleanup_errors']=list(group.cleanup_errors) if group is not None else []
            if guard is not None:
                report.update(guard.restore())
            else:
                report['configuration_restored']=True  # 尚未登记/安装任何临时变体。
            report['resource_release_errors']=release_errors
        report['loopback_requests']=received
        report['inventory_send_interval']=inventory_send_interval
        report['transport_sessions']=transport_sessions
        report['native_transport_challenge_verified']=any(row.get('challenge_verified') for row in transport_sessions)
        report['native_new_key_application_received']=any(row.get('new_key_application_received') for row in transport_sessions)
        try:
            from tools.native_client_logs import capture_logs
            report['client_log_records']=capture_logs(root,output,log_baseline)
        except Exception as error:
            report['log_collection_error']=type(error).__name__
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def _verify_current_dev_view(current, state, identity):
    """校验同一次fresh me快照的邮箱及绑定；只返回脱敏证明，不能将历史文件当作当前身份。"""
    if not isinstance(current,dict) or current.get('email_verified') is not True:
        raise ValueError('CURRENT_DEV_EMAIL_NOT_VERIFIED')
    expected_account=state.get('account_id')
    if not isinstance(expected_account,str) or not expected_account or current.get('id')!=expected_account:
        raise ValueError('CURRENT_DEV_ACCOUNT_MISMATCH')
    if not isinstance(identity,dict) or identity.get('account_id')!=expected_account:
        raise ValueError('REDEEMED_DEV_ACCOUNT_MISMATCH')
    # 同时绑定独立游戏整数 ID，拒绝 bool/字符串等可宽松比较的输入，避免账号与原生 Uin 错配。
    expected_game=state.get('game_id')
    if (type(expected_game) is not int or expected_game<1
            or type(current.get('game_id')) is not int or current['game_id']!=expected_game
            or type(identity.get('game_id')) is not int or identity['game_id']!=expected_game):
        raise ValueError('CURRENT_DEV_GAME_ID_MISMATCH')
    # 公开证明只记录比较结果，不携带邮箱、账号 ID、凭据或服务端完整读取视图。
    return {'source':'CURRENT_API_VIEWS_ME_AND_REDEEMED_IDENTITY',
            'email_verified':True,'acceptance_account_matches':True,
            'game_identity_account_matches':True,'game_id_matches':True}


def verify_current_dev_identity(client, session, state, identity):
    """启动前实时核对邮箱所有权和账号绑定；失败仅抛固定错误，原接口保持脱敏返回。"""
    return _verify_current_dev_view(client.request('/api/views/me',token=session),state,identity)


def current_dev_player_info(client, session, state, identity, *, code_page, require_empty_inventory=False):
    """从同一次实时读取取得已绑定业务角色原昵称；校验身份及角色ID后严格编码，不输出昵称。"""
    from adapters.cn_current.zone import encode_current_player_nickname
    current=client.request('/api/views/me',token=session)
    verification=_verify_current_dev_view(current,state,identity)
    character=current.get('character')
    if (not isinstance(character,dict) or not state.get('character_id')
            or character.get('id')!=state['character_id'] or character.get('account_id')!=state['account_id']):
        raise ValueError('CURRENT_DEV_CHARACTER_MISMATCH')
    if require_empty_inventory and (not isinstance(current.get('inventory'),list) or current['inventory']):
        raise ValueError('CURRENT_DEV_INVENTORY_NOT_EMPTY')
    nickname=encode_current_player_nickname(character.get('nickname'),code_page=code_page)
    return verification,nickname


def revoke_owned_dev_session(client, session, *, game_token=None, client_sha=None):
    """注销本探针来源会话并用原凭据做拒绝回读；网络故障不能被标记为凭据已失效。

    不保存令牌、不重复登录、不撤销其他账号。即使注销应答丢失，两个只读入口均明确AUTH
    拒绝才能证明来源/游戏凭据失效；其他异常只记录类型，供恢复后通过正常账号登录收回。
    """
    from tools.email_launcher import LoginError
    from adapters.email_protocol import PROTOCOL
    result={'logout_ack':False,'source_rejected':False,'game_rejected':None,'errors':[]}
    try:
        result['logout_ack']=client.request('/api/logout',{},session).get('logged_out') is True
    except Exception as error:
        result['errors'].append({'step':'logout','type':type(error).__name__})
    requests=[('source_rejected','/api/views/me',None,session)]
    if game_token is not None:
        requests.append(('game_rejected','/api/game/session',
                         {'protocol':PROTOCOL,'client_sha256':client_sha},game_token))
        result['game_rejected']=False
    for label,path,data,credential in requests:
        try:
            client.request(path,data,credential)
        except LoginError as error:
            # 只有服务明确AUTH拒绝；连接失败、邮箱策略、服务不可用等都不能代称注销成功。
            if str(error)=='AUTH':
                result[label]=True
            else:
                result['errors'].append({'step':label,'type':'NON_AUTH_REJECTION'})
        except Exception as error:
            result['errors'].append({'step':label,'type':type(error).__name__})
    result['verified']=result['source_rejected'] and (game_token is None or result['game_rejected'])
    return result


def main():
    """诊断入口只使用明确的本地研究身份；正式身份应由调用方先兑换独立邮箱票据再传入。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--seconds',type=int,default=60,help='Owned game observation duration: 1..300 seconds, default 60')
    parser.add_argument('--windowed',action='store_true',help='Request a 1280x720 window using engine startup arguments; does not change OS display settings')
    parser.add_argument('--server-map',help='Research traditional engine server entry through the same verified independent IPC; map/mode must match the fingerprinted current config, not a usable-node declaration')
    parser.add_argument('--defer-player-info-after-version',action='store_true',help='Diagnostic only: publish profile once one second after A205 and observed FF05; does not reply to or modify the version notification; requires --serve-player-info')
    parser.add_argument('--continuous-zone',action='store_true',help='Continue existing Zone handlers for the whole bounded game observation, up to 2048 frames; requires verified Zone login, unknown messages remain unanswered')
    parser.add_argument('--probe-role-resources',action='store_true',help='Temporary two-role resource smoke fixture, never real inventory; requires the verified fresh empty DEV inventory and --serve-player-props')
    parser.add_argument('--serve-role-equip-probe',action='store_true',help='Reply only to exact Equip of the owned temporary role fixture; requires --probe-role-resources, captures at most 64 frames within 15 seconds')
    parser.add_argument('--serve-detailed-channel',action='store_true',help='Reply to current A357 with the native-serializer-verified one-zone/one-channel loopback A358 research directory; requires --serve-player-props')
    parser.add_argument('--native-inventory',action='store_true',help='Project current owned MySQL inventory; excludes temporary role fixtures')
    parser.add_argument('--native-features',action='store_true',help='Enable current verified feature codecs and paged asset API; live acceptance remains separate')
    parser.add_argument('--native-settings',action='store_true',help='Load/save opaque settings using the authenticated game API and acknowledge A202 only after verified MySQL commit; requires DEV identity and player define exchange')
    parser.add_argument('--refresh-role-probe',action='store_true',help='After exact temporary equipment reply, refresh A005 with the existing probe role GID; requires --serve-role-equip-probe, never real inventory')
    parser.add_argument('--serve-zone-hints',action='store_true',help='After the isolated role refresh send the current DLL verified FF13 with explicit zero hint; requires --refresh-role-probe, no lobby readiness claim')
    parser.add_argument('--disable-screen-texture',action='store_true',help='Temporarily disable only the fingerprinted current UI screen texture flag via persistent config guard; diagnostic only, not a verified fix')
    parser.add_argument('--sample-threads',action='store_true',help='Briefly sample only this Job thread locations; always resume immediately')
    parser.add_argument('--redirect-game-network',action='store_true',help='Temporarily redirect only the fingerprinted current Geo/Zone defaults to the owned loopback listener')
    parser.add_argument('--discover-login-references',action='store_true',help='Search only executable pages for fixed current-build login literal references; requires thread sampling')
    parser.add_argument('--verify-native-challenge',action='store_true',help='Validate only the current-build native AUTH and AES SYN/SYNACK against this probe identity; no application responses')
    parser.add_argument('--capture-application',action='store_true',help='After verified AUTH and challenge, send a current-build research key change and privately capture one application request; no business responses')
    parser.add_argument('--serve-geo-ping',action='store_true',help='After verified current PingList request, serve one explicitly versioned local research PingList response and capture the next request')
    parser.add_argument('--serve-geo-zone',action='store_true',help='Serve one current-build local ZoneList entry and privately capture the next authenticated connection; no Zone business responses')
    parser.add_argument('--capture-zone-sequence',action='store_true',help='Capture Zone requests within 15 seconds: 8 by default, 16 with player info, 32 with empty props; responses require separate explicit flags')
    parser.add_argument('--serve-zone-login',action='store_true',help='Reply once with a native-serializer-verified research login response; requires verified DEV email identity, no role or asset projection')
    parser.add_argument('--serve-player-define',action='store_true',help='Reply once with the explicit empty player settings research profile after verified login; no stored settings changes')
    parser.add_argument('--serve-player-info',action='store_true',help='After player define send the current verified DEV nickname/game ID with explicit empty-assets research fields; requires current ACP 936')
    parser.add_argument('--serve-player-props',action='store_true',help='Send one verified empty A006 last-package only when the fresh DEV inventory is empty; requires player info research')
    parser.add_argument('--dev-email-state',action='store_true',help='Use only the owned, verified DEV acceptance account and revoke source session after the probe')
    parser.add_argument('--manual-test',action='store_true',help='Explicit user handoff: keep game and bridge running until user exits; requires DEV inventory and continuous Zone')
    args=parser.parse_args()
    if not 1 <= args.seconds <= MAX_BOOT_SECONDS:
        parser.error('--seconds must be within 1..300')
    if args.manual_test and not (args.dev_email_state and args.native_inventory and args.continuous_zone):
        parser.error('--manual-test requires --dev-email-state --native-inventory --continuous-zone')
    if args.discover_login_references and not args.sample_threads:
        parser.error('--discover-login-references requires --sample-threads')
    if args.capture_application and not args.verify_native_challenge:
        parser.error('--capture-application requires --verify-native-challenge')
    if args.serve_geo_ping and not args.capture_application:
        parser.error('--serve-geo-ping requires --capture-application')
    if args.serve_geo_zone and not args.serve_geo_ping:
        parser.error('--serve-geo-zone requires --serve-geo-ping')
    if args.capture_zone_sequence and not args.serve_geo_zone:
        parser.error('--capture-zone-sequence requires --serve-geo-zone')
    if args.serve_zone_login and not (args.capture_zone_sequence and args.dev_email_state):
        parser.error('--serve-zone-login requires --capture-zone-sequence and --dev-email-state')
    if args.continuous_zone and not args.serve_zone_login:
        parser.error('--continuous-zone requires --serve-zone-login')
    if args.serve_player_define and not args.serve_zone_login:
        parser.error('--serve-player-define requires --serve-zone-login')
    if args.serve_player_info and not (args.serve_player_define and args.dev_email_state):
        parser.error('--serve-player-info requires --serve-player-define and --dev-email-state')
    if args.serve_player_props and not args.serve_player_info:
        parser.error('--serve-player-props requires --serve-player-info')
    if args.probe_role_resources and not args.serve_player_props:
        parser.error('--probe-role-resources requires --serve-player-props')
    if args.serve_role_equip_probe and not args.probe_role_resources:
        parser.error('--serve-role-equip-probe requires --probe-role-resources')
    if args.serve_detailed_channel and not args.serve_player_props:
        parser.error('--serve-detailed-channel requires --serve-player-props')
    if args.native_settings and not (args.serve_player_define and args.dev_email_state):
        parser.error('--native-settings requires --serve-player-define and --dev-email-state')
    if args.refresh_role_probe and not args.serve_role_equip_probe:
        parser.error('--refresh-role-probe requires --serve-role-equip-probe')
    if args.serve_zone_hints and not args.refresh_role_probe and not args.native_inventory:
        parser.error('--serve-zone-hints requires --refresh-role-probe')
    if args.native_inventory and (not args.dev_email_state or not args.serve_player_props or args.probe_role_resources):
        parser.error('--native-inventory requires DEV/player-props and excludes temporary role fixtures')
    if args.native_features and not args.native_inventory:
        parser.error('--native-features requires --native-inventory')
    if args.server_map and not args.dev_email_state:
        parser.error('--server-map requires the current verified DEV email identity')
    if args.defer_player_info_after_version and not args.serve_player_info:
        parser.error('--defer-player-info-after-version requires --serve-player-info')
    if args.dev_email_state:
        from tools.email_launcher import Client, fingerprint
        from tools.verify_email_dev import metadata
        from revival.security import token_hash
        from adapters.email_protocol import PROTOCOL
        import secrets
        root=Path(__file__).resolve().parents[1]
        state=json.loads((root/'runtime/mail-acceptance.json').read_text(encoding='utf-8'))
        if not all(state.get(key) for key in ('registered','email_verified','reset_verified')):
            raise ValueError('Owned DEV acceptance identity is not ready')
        client=Client('http://127.0.0.1:18765')
        client_sha,_=fingerprint(args.workspace)
        session=client.request('/api/player/login',dict(email=state['email'],password=state['password']))['token']
        result,game=None,None
        try:
            verifier=secrets.token_urlsafe(32)
            ticket=client.request('/api/launcher/tickets',metadata(protocol=PROTOCOL,client_sha256=client_sha,
                verifier_sha256=token_hash(verifier)),session)
            game=client.request('/api/launcher/redeem',dict(protocol=PROTOCOL,client_sha256=client_sha,
                verifier=verifier,ticket=ticket['ticket']))
            identity=client.request('/api/game/session',dict(protocol=PROTOCOL,client_sha256=client_sha),game['game_token'])
            # 每次本机启动都重新读取当前状态；换绑或重新校验中的账号不能沿用私人文件的历史成功标记。
            # 该检查位于 finally 注销范围内，当前 me 失败、账号不符或 probe 异常均撤销本次来源会话。
            projection={'windowed':args.windowed,'disable_screen_texture':args.disable_screen_texture,
                        'serve_zone_hints':args.serve_zone_hints,'server_map':args.server_map,
                        'defer_player_info_after_version':args.defer_player_info_after_version,
                        'continuous_zone':args.continuous_zone,'manual_test':args.manual_test}
            if args.serve_detailed_channel:
                projection['serve_detailed_channel']=True
            if args.probe_role_resources:
                projection['role_resource_probe']=True
                projection['role_equip_probe']=args.serve_role_equip_probe
                projection['refresh_role_probe']=args.refresh_role_probe
            if args.serve_player_info:
                verification,nickname=current_dev_player_info(client,session,state,identity,
                    code_page=ctypes.WinDLL('kernel32').GetACP(),require_empty_inventory=args.serve_player_props and not args.native_inventory)
                projection['player_info_nickname']=nickname
                projection['player_props_empty']=args.serve_player_props
            else:
                verification=verify_current_dev_identity(client,session,state,identity)
            if args.native_settings:
                from tools.native_settings_bridge import NativeSettingsBridge
                projection['native_settings']=NativeSettingsBridge(client,game['game_token'],client_sha)
            if args.native_inventory:
                from tools.native_inventory_bridge import NativeInventoryBridge
                projection['native_inventory']=NativeInventoryBridge(client,game['game_token'],client_sha,identity['game_id'],paged=args.native_features)
                if args.native_features:
                    from tools.native_feature_bridge import NativeFeatureBridge
                    bridge=projection['native_inventory']
                    bridge.features=NativeFeatureBridge(bridge,Path(__file__).resolve().parents[1]/'runtime/native-operations'/f"{identity['game_id']}.json")
            result=probe(args.workspace,identity['game_id'],game['game_token'],args.output,args.seconds,args.sample_threads,
                         args.redirect_game_network,args.discover_login_references,args.verify_native_challenge,args.capture_application,args.serve_geo_ping,args.serve_geo_zone,args.capture_zone_sequence,args.serve_zone_login,args.serve_player_define,**projection)
            result['server_email_identity_verified']=verification['email_verified']
            result['server_email_identity_verification']=verification
            (args.output/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in result.items() if k!='loaded_modules'}))
        finally:
            cleanup=revoke_owned_dev_session(client,session,
                game_token=game['game_token'] if game is not None else None,client_sha=client_sha)
            if args.output.is_dir():
                (args.output/'session-cleanup.json').write_text(json.dumps(cleanup,indent=2),encoding='utf-8')
            if result is not None:
                result['source_session_cleanup']=cleanup
                (args.output/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            if args.manual_test and args.output.is_dir():
                (args.output/'manual-test-state.json').write_text(json.dumps(dict(state='STOPPED',
                    automatic_time_limit=False,source_and_game_revoked=cleanup['verified'],
                    cleanup_pending=not cleanup['verified'])),encoding='utf-8')
            if not cleanup['verified']:
                # 保留探针本来的异常；没有原异常时让CLI显式失败，不能仅凭Job清理宣称全部清理成功。
                import sys
                if sys.exc_info()[0] is None:
                    raise RuntimeError('OWNED_SOURCE_SESSION_REVOKE_NOT_VERIFIED')
    else:
        print(json.dumps(probe(args.workspace,10001,'NZ_OWNED_LOOPBACK_RESEARCH_ONLY',args.output,args.seconds,args.sample_threads,
                              args.redirect_game_network,args.discover_login_references,args.verify_native_challenge,args.capture_application,args.serve_geo_ping,args.serve_geo_zone,args.capture_zone_sequence,args.serve_zone_login,args.serve_player_define,windowed=args.windowed,disable_screen_texture=args.disable_screen_texture,server_map=args.server_map)))


if __name__=='__main__':
    main()
