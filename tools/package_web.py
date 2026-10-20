"""打包可审查的 Web 发布归档；仅白名单源码和 Vite 产物，凭据/数据库/原客户端不入包。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def collect(root):
    """收集固定运行目录与安装工具，拒绝链接和任何疑似数据库/秘密文件。"""
    files = []
    for name in ('revival', 'nzproject', 'adapters', 'config', 'admin/dist', 'deploy'):
        for path in (root/name).rglob('*'):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('WEB_PACKAGE_LINK_REJECTED')
            if path.is_dir() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            lowered = path.name.lower()
            # 受保护配置常名为monitor.env/user-mysql.env，不能只检查以.env开头的文件。
            # SQLite旁文件和常见Windows/SSH私钥容器也不属于Web发布内容。
            if (lowered.startswith('.env') or '.env.' in lowered
                    or path.suffix.lower() in ('.env','.db','.sqlite','.sqlite3','.pem','.key','.pfx','.p12','.ppk')
                    or lowered.endswith(('.db-wal','.db-shm','.sqlite-journal','.sqlite3-journal'))
                    or lowered in ('id_rsa','id_ecdsa','id_ed25519','credentials','dev环境服务器.txt','房间服务器配置.txt')):
                raise ValueError('WEB_PACKAGE_PRIVATE_FILE_REJECTED')
            files.append(path)
    for name in ('manage.py', 'requirements.txt', 'requirements-monitoring.txt', 'pyproject.toml',
            'tools/monitor_agent.py', 'docs/WEB_DEPLOYMENT.md'):
        path = root/name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('WEB_PACKAGE_REQUIRED_FILE_INVALID')
        files.append(path)
    if not (root/'admin/dist/index.html').is_file():
        raise ValueError('BUILD_FRONTEND_FIRST')
    return sorted(set(files), key=lambda path:path.relative_to(root).as_posix())


def package(root, output):
    """输出确定性tar、逐文件清单和归档摘要；不连接服务器，不复制运行目录或环境文件。"""
    files = collect(root)
    manifest = {'format':'nz-web-release-v1', 'source_commit':subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=root,text=True).strip(), 'source_state':'WORKING_TREE',
        'game_server_included':False, 'credentials_included':False,
        'files':[{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
    frozen = json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('wb') as stream:
        with gzip.GzipFile(filename='',fileobj=stream,mode='wb',mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w') as bundle:
                for path in files:
                    info = bundle.gettarinfo(str(path),arcname=path.relative_to(root).as_posix())
                    info.uid=info.gid=info.mtime=0
                    info.uname=info.gname=''
                    info.mode=0o755 if path.suffix=='.sh' else 0o644
                    with path.open('rb') as content:
                        bundle.addfile(info,content)
                info=tarfile.TarInfo('RELEASE.json');info.size=len(frozen);info.mtime=0;info.mode=0o644
                bundle.addfile(info,io.BytesIO(frozen))
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    output.with_suffix(output.suffix+'.manifest.json').write_bytes(frozen)
    return {'archive':str(output),'sha256':digest,'bytes':output.stat().st_size,'files':len(files)}


def main():
    """默认把Web归档放入本批交付目录；--output只指定产物位置，不改变收集白名单。"""
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=root/'outputs/01a15734-c809-7ff2-8313-f617805a4441/nz-web-platform-2026-10-19.tar.gz')
    args=parser.parse_args()
    print(json.dumps(package(root,args.output.resolve()),ensure_ascii=False))


if __name__=='__main__':
    main()
