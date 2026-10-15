"""独立邮箱身份的原生启动研究：自有子进程 PID 映射、回环请求采集、可恢复配置与限时清理。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
from adapters.cn_current.email_ipc import research_mapping, PROXY_SHA256, LOOPBACK_IPC_PROFILE
from adapters.cn_current.transport import TRANSPORT_PROFILE
from tools.client_workspace import digest
from tools.native_recovery import validate
from tools.native_job import JobGroup
from tools.native_probe import loaded_modules


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
    def __init__(self, binary, game_id, directory, output):
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
        block=ctypes.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(environment.items(),key=lambda row:row[0].upper()))+'\0\0')
        command=ctypes.create_unicode_buffer(subprocess.list2cmdline([str(binary),'-q',str(game_id),
            '-log','-FORCELOGFLUSH','-nomoviestartup','-ABSLOG='+str(output.resolve()/'engine.log')]))
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
    """同一执行环境先只读查询 x86 D3D9；缺少图形能力时立即给出原因，避免再次停在不可见弹窗。"""
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


def probe(root, game_id, game_token, output, seconds=60, sample_threads=False, redirect_game_network=False,
          discover_login_references=False, verify_native_challenge=False, capture_application=False, serve_geo_ping=False,
          serve_geo_zone=False, capture_zone_sequence=False, serve_zone_login=False, serve_player_define=False):
    """核对当前样本并启动真实 TGame；采集首个回环请求，不臆造未知 ROLE/ZONE 响应。"""
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
    root=root.resolve(strict=True)
    metadata,_=validate(root)
    if output.exists() or not output.resolve().is_relative_to(Path(__file__).resolve().parents[1]/'evidence/private'):
        raise ValueError('Native boot evidence requires a new private output directory')
    expected={'TGame.exe':'b81bb4e1256ad1fca1ecbbf93b35979d96c36ca16f8bbfc8d126e9a452df51f8',
              'TGameBase.dll':'79894ee3b24ad12610c23a385f66820ec4bc6ab9049c57ff5867bcdf1462b6ca',
              'GameLoader.exe':'c86c1f63ff24f33da42e7ef358150f0abef68954d2b137df3b5b4c958edd10d1',
              'ProtocalHandler.dll':'e28a7da8e6668feb65f5573819714738bce7210b79c49dfe287a63f2be3da228',
              'TenProxy.dll':PROXY_SHA256}
    binary=root/'Binaries/Win32/TGame.exe'
    if not 1 <= seconds <= 180 or any(digest(binary.parent/name)!=sha for name,sha in expected.items()):
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
    before={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
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
                    reader=CapturedFrameStream(connection)
                    request=receive_frame(reader); save_packet(request,'client_to_probe'); reader=None
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
                            sequence_deadline=time.monotonic()+15
                            for sequence in range(1,9):
                                if stop.is_set() or time.monotonic()>=sequence_deadline:
                                    session['zone_sequence_stop_reason']='PROBE_STOPPED' if stop.is_set() else 'TIME_LIMIT'
                                    break
                                session['stage']='await_zone_application_sequence'
                                reader=CapturedFrameStream(connection,seconds=sequence_deadline-time.monotonic())
                                try:
                                    extra_frame=receive_application_frame(reader)
                                except TimeoutError:
                                    # 本模式故意不发送登录响应；无新字节的等待结束不是客户端协议失败。
                                    # 部分帧超时仍交给外层保存前缀和错误，不能把截断数据掩盖为正常停止。
                                    if reader.content:
                                        raise
                                    reader=None
                                    session['zone_sequence_stop_reason']='IDLE_TIME_LIMIT' if session.get('zone_login_response_sent') else 'IDLE_TIME_LIMIT_NO_RESPONSE_SENT'
                                    break
                                save_packet(extra_frame,'client_to_probe'); reader=None
                                extra=decode_application(extra_frame,new_key)
                                (output/f'application-{number:02d}-sequence-{sequence:02d}.bin').write_bytes(extra)
                                extra_summary={'bytes':len(extra),'sha256':hashlib.sha256(extra).hexdigest()}
                                header=parse_zone_header(extra)
                                extra_summary['header']={key:header[key] for key in ('magic','command','head_length','body_length')}
                                if header['command']==0xA000:
                                    extra_summary['structure']=parse_login_request(extra)
                                    session['zone_login_request_verified']=True
                                session['zone_following_applications'].append(extra_summary)
                                if serve_zone_login and header['command']==0xA000:
                                    if session.get('zone_login_response_sent'):
                                        raise TransportError('DUPLICATE_ZONE_LOGIN_REQUEST')
                                    from adapters.cn_current.zone import build_research_login_response, ZONE_LOGIN_RESPONSE_PROFILE
                                    from adapters.cn_current.transport import make_application_response
                                    # 此固定响应仅验证当前消费者分支，无库存赠送、钱包映射、建角或大厅成功声明。
                                    login_response=build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE)
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
                                if serve_player_define and header['command']==0xA204:
                                    if not session.get('zone_login_response_sent') or session.get('player_define_response_sent'):
                                        raise TransportError('UNEXPECTED_PLAYER_DEFINE_REQUEST_STATE')
                                    from adapters.cn_current.zone import (parse_player_define_request,
                                        build_research_empty_player_define_response, ZONE_EMPTY_PLAYER_DEFINE_PROFILE)
                                    extra_summary['structure']=parse_player_define_request(extra)
                                    # 空资料只走当前已证的零长度完成分支；不覆盖真实玩家自定义数据或生成未知版本内容。
                                    define_response=build_research_empty_player_define_response(profile=ZONE_EMPTY_PLAYER_DEFINE_PROFILE)
                                    define_frame=make_application_response(define_response,new_key)
                                    connection.sendall(define_frame); save_packet(define_frame,'probe_to_client')
                                    (output/f'player-define-response-{number:02d}.bin').write_bytes(define_response)
                                    session['player_define_response_profile']=ZONE_EMPTY_PLAYER_DEFINE_PROFILE
                                    session['player_define_response_sent']=True
                                    session['player_define_response_bytes']=len(define_response)
                                    session['player_define_response_sha256']=hashlib.sha256(define_response).hexdigest()
                            session['stage']='zone_application_sequence_captured'
                            # 有界采集结束不等于服务端踢人；保持本次已认证连接到主探针清理，
                            # 避免人为EOF触发当前尚未实现的重连IDENT，使后续记录混成登录失败。
                            session['connection_held_after_capture']=True
                            stop.wait(seconds+5)
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
                    session['error_type']=type(error).__name__
                    if isinstance(error,TransportError):
                        session['error_code']=str(error)  # 该模块只抛固定错误码，不拼入输入材料。
                    if reader is not None and reader.content:
                        try: save_packet(bytes(reader.content),'client_to_probe_rejected_prefix')
                        except Exception as evidence_error:
                            session['failure_evidence_error_type']=type(evidence_error).__name__
    worker=threading.Thread(target=capture,daemon=True); worker.start()
    guard=None
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
            process=SuspendedClient(binary,game_id,binary.parent,output)
            group.attach(process)
            # 当前 GameLoader 的转交路径复制 0x80000 字节，映射容量不能沿用 DLL 单独自测的 64KiB。
            mapping=mmap.mmap(-1,0x80000,tagname='TCLS_SHAREDMEMEMORY'+str(process.pid),access=mmap.ACCESS_WRITE)
            mapping.write(data); mappings[process.pid]=mapping
            process.resume(); report['game_process_started']=True
            started=time.monotonic(); deadline=started+seconds; next_network_sample=started
            report['tcp_samples']=[]
            last_pids=[]
            while time.monotonic()<deadline:
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
                    from tools.native_tcp_probe import sample_owned_tcp
                    snapshot=sample_owned_tcp(group)
                    snapshot['elapsed_seconds']=round(time.monotonic()-started,3)
                    report['tcp_samples'].append(snapshot)
                    next_network_sample=time.monotonic()+2
                time.sleep(0.01)
            report['observed_pids']=sorted(mappings)
            report['loaded_modules']={str(pid):loaded_modules(pid) for pid in group.pids()}
            if sample_threads:
                from tools.native_thread_probe import sample_owned_threads
                report['thread_sample']=sample_owned_threads(group,output,discover_login_references,(game_id,port))
            report['timed_out']=bool(group.pids())
            if group.pids(): group.terminate()
            process.wait()
            time.sleep(0.1)
            report['cleanup_verified']=not group.pids()
            report['exit_codes']={str(pid):hex(code) for pid,code in group.collect_exits(timeout_ms=1000).items()}
    finally:
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
            report['cleanup_verified']=bool(report.get('cleanup_verified') and not worker.is_alive())
            if guard is not None:
                report.update(guard.restore())
            else:
                report['configuration_restored']=True  # 尚未登记/安装任何临时变体。
            report['resource_release_errors']=release_errors
        report['loopback_requests']=received
        report['transport_sessions']=transport_sessions
        report['native_transport_challenge_verified']=any(row.get('challenge_verified') for row in transport_sessions)
        report['native_new_key_application_received']=any(row.get('new_key_application_received') for row in transport_sessions)
        try:
            for path in root.rglob('*'):
                if path.is_file() and path.suffix.lower() in ('.log','.txt') and path.stat().st_size < 10*1024*1024:
                    if before.get(path)!=(path.stat().st_size,path.stat().st_mtime_ns):
                        target=output/'client-output'/path.relative_to(root)
                        target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(path.read_bytes())
        except Exception as error:
            report['log_collection_error']=type(error).__name__
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    """诊断入口只使用明确的本地研究身份；正式身份应由调用方先兑换独立邮箱票据再传入。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--sample-threads',action='store_true',help='Briefly sample only this Job thread locations; always resume immediately')
    parser.add_argument('--redirect-game-network',action='store_true',help='Temporarily redirect only the fingerprinted current Geo/Zone defaults to the owned loopback listener')
    parser.add_argument('--discover-login-references',action='store_true',help='Search only executable pages for fixed current-build login literal references; requires thread sampling')
    parser.add_argument('--verify-native-challenge',action='store_true',help='Validate only the current-build native AUTH and AES SYN/SYNACK against this probe identity; no application responses')
    parser.add_argument('--capture-application',action='store_true',help='After verified AUTH and challenge, send a current-build research key change and privately capture one application request; no business responses')
    parser.add_argument('--serve-geo-ping',action='store_true',help='After verified current PingList request, serve one explicitly versioned local research PingList response and capture the next request')
    parser.add_argument('--serve-geo-zone',action='store_true',help='Serve one current-build local ZoneList entry and privately capture the next authenticated connection; no Zone business responses')
    parser.add_argument('--capture-zone-sequence',action='store_true',help='Passively capture up to eight more Zone application frames within fifteen seconds; no Zone responses')
    parser.add_argument('--serve-zone-login',action='store_true',help='Reply once with a native-serializer-verified research login response; requires verified DEV email identity, no role or asset projection')
    parser.add_argument('--serve-player-define',action='store_true',help='Reply once with the explicit empty player settings research profile after verified login; no stored settings changes')
    parser.add_argument('--dev-email-state',action='store_true',help='Use only the owned, verified DEV acceptance account and revoke source session after the probe')
    args=parser.parse_args()
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
    if args.serve_player_define and not args.serve_zone_login:
        parser.error('--serve-player-define requires --serve-zone-login')
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
        try:
            verifier=secrets.token_urlsafe(32)
            ticket=client.request('/api/launcher/tickets',metadata(protocol=PROTOCOL,client_sha256=client_sha,
                verifier_sha256=token_hash(verifier)),session)
            game=client.request('/api/launcher/redeem',dict(protocol=PROTOCOL,client_sha256=client_sha,
                verifier=verifier,ticket=ticket['ticket']))
            identity=client.request('/api/game/session',dict(protocol=PROTOCOL,client_sha256=client_sha),game['game_token'])
            result=probe(args.workspace,identity['game_id'],game['game_token'],args.output,args.seconds,args.sample_threads,
                         args.redirect_game_network,args.discover_login_references,args.verify_native_challenge,args.capture_application,args.serve_geo_ping,args.serve_geo_zone,args.capture_zone_sequence,args.serve_zone_login,args.serve_player_define)
            result['server_email_identity_verified']=True
            (args.output/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in result.items() if k!='loaded_modules'}))
        finally:
            client.request('/api/logout',{},session)
    else:
        print(json.dumps(probe(args.workspace,10001,'NZ_OWNED_LOOPBACK_RESEARCH_ONLY',args.output,args.seconds,args.sample_threads,
                              args.redirect_game_network,args.discover_login_references,args.verify_native_challenge,args.capture_application,args.serve_geo_ping,args.serve_geo_zone,args.capture_zone_sequence,args.serve_zone_login,args.serve_player_define)))


if __name__=='__main__':
    main()
