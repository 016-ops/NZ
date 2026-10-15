#!/usr/bin/env bash
# DEV 首次安装/发布：只管理带标记的独立目录和 nz-dev-* 服务，数据库仅向前迁移。
set -euo pipefail
umask 027
release_id=${1:?release id required}
archive_sha=${2:?archive sha256 required}
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}-[a-f0-9]{12}$ ]]
[[ "$archive_sha" =~ ^[a-f0-9]{64}$ ]]
base=/opt/nz-revival
archive=/tmp/nz-revival-$release_id.tar.gz
environment=/tmp/nz-revival-$release_id.env
if [[ -e "$base" && ! -f "$base/.nz-dev-managed" ]]; then
    echo 'Refusing an unmanaged existing project directory' >&2
    exit 1
fi
for unit in /etc/systemd/system/nz-dev-{current,pve}{,-maintain}.service; do
    if [[ -e "$unit" ]] && ! head -n 1 "$unit" | grep -qx '# Managed by NZ Revival DEV'; then
        echo 'Refusing an unmanaged systemd unit' >&2
        exit 1
    fi
done
echo "$archive_sha  $archive" | sha256sum --check --status
# 系统仅补充编译 MySQL 驱动和逻辑备份所需组件，不安装或替换 MySQL 服务。
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev default-libmysqlclient-dev default-mysql-client pkg-config build-essential ca-certificates curl libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev uuid-dev
id nz-revival >/dev/null 2>&1 || useradd --system --home-dir "$base/shared" --shell /usr/sbin/nologin nz-revival
install -d -m 0755 "$base" "$base/releases"
install -d -m 0750 -o root -g nz-revival "$base/shared"
touch "$base/.nz-dev-managed"
release=$base/releases/$release_id
[[ ! -e "$release" ]]
mkdir -m 0755 "$release"
# 归档由固定白名单创建，这里再次拒绝路径穿越、链接和设备文件。
python3 - "$archive" "$release" <<'PY'
import pathlib,sys,tarfile
with tarfile.open(sys.argv[1]) as bundle:
    for member in bundle.getmembers():
        path=pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise ValueError('Unsafe release archive entry')
    bundle.extractall(sys.argv[2])
PY
chmod -R a+rX "$release"
# DEV 无法直连 GitHub 发行下载，改用 python.org 固定源版本构建到项目目录；系统 Python 保持原状。
python=$base/shared/python312/bin/python3.12
if [[ ! -x "$python" ]]; then
    build=$base/shared/python-build
    mkdir -p "$build"
    curl --fail --location --retry 2 --continue-at - --connect-timeout 10 --max-time 900 \
        https://www.python.org/ftp/python/3.12.12/Python-3.12.12.tar.xz -o "$build/Python-3.12.12.tar.xz"
    sha256sum "$build/Python-3.12.12.tar.xz" > "$build/source.sha256"
    tar -xJf "$build/Python-3.12.12.tar.xz" -C "$build"
    (cd "$build/Python-3.12.12" && ./configure --prefix="$base/shared/python312" --with-ensurepip=install && make -j2 && make install)
fi
"$python" -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install --disable-pip-version-check -r "$release/requirements.txt"
chmod -R a+rX "$release"
# 环境文件只有服务组可读；凭据从未出现在 shell 参数或日志中。
install -m 0640 -o root -g nz-revival "$environment" "$base/shared/mysql.env"
if [[ -f /tmp/nz-revival-$release_id.mail.env ]]; then
    # 原发信配置备份只留受保护 shared；发布源码中不保存 SMTP 密码。
    if [[ -f "$base/shared/mail.env" ]]; then cp -p "$base/shared/mail.env" "$base/shared/mail-before-$release_id.env"; fi
    install -m 0640 -o root -g nz-revival /tmp/nz-revival-$release_id.mail.env "$base/shared/mail.env"
    rm -- /tmp/nz-revival-$release_id.mail.env
fi
chmod 0750 "$base/shared"
chmod -R a+rX "$base/shared/python312"
for profile in current pve; do
    if [[ "$profile" = current ]]; then mode=current; pve=0; port=18765; else mode=synthetic; pve=1; port=18767; fi
    # 迁移失败时不切换 current 链接、不重启旧服务；已有业务数据不回退。
    runuser -u nz-revival -- env NZ_ENV_FILE="$base/shared/mysql.env" NZ_PROFILE="$mode" NZ_PVE_TEST_PROFILE="$pve" \
        "$release/.venv/bin/python" "$release/manage.py" migrate --noinput
    for kind in serve maintain; do
        suffix=''; args="serve --port $port"
        if [[ "$kind" = maintain ]]; then suffix=-maintain; args=maintain; fi
        unit=nz-dev-$profile$suffix.service
        cat > /etc/systemd/system/$unit <<EOF
# Managed by NZ Revival DEV
[Unit]
Description=NZ DEV $profile $kind (loopback only)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=nz-revival
Group=nz-revival
WorkingDirectory=$base/current
Environment=NZ_ENV_FILE=$base/shared/mysql.env
Environment=NZ_MAIL_ENV_FILE=$base/shared/mail.env
Environment=NZ_PROFILE=$mode
Environment=NZ_PVE_TEST_PROFILE=$pve
Environment=NZ_PORT=$port
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$base/current/.venv/bin/python $base/current/manage.py $args
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$base/shared
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
    done
done
if [[ -L "$base/current" ]]; then readlink "$base/current" > "$base/shared/previous-release"; fi
ln -s "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
systemctl daemon-reload
systemctl enable nz-dev-current.service nz-dev-current-maintain.service nz-dev-pve.service nz-dev-pve-maintain.service
systemctl restart nz-dev-current.service nz-dev-current-maintain.service nz-dev-pve.service nz-dev-pve-maintain.service
# 删除的只是此次上传且已验证的临时凭据；持久配置留在受保护的 shared 下。
rm -- "$environment"
echo 'NZ_DEV_INSTALL_FINISHED'
