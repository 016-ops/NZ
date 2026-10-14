"""本地生命周期命令；凭据交互输入，服务与维护使用独立进程。"""
import getpass
import json
import time
from urllib.parse import quote
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connections
from revival.service import Service


# 兼容入口与 manage.py 共用生命周期命令；operation 决定参数和行为，避免重复实现。
class LocalCommand(BaseCommand):
    """兼容入口与 manage.py 共用生命周期命令；operation 决定参数和行为，避免重复实现。"""
    operation = None

    # 仅注册本命令需要的参数，profile 与数据库连接在 Django 初始化前由环境配置确定。
    def add_arguments(self, parser):
        """仅注册本命令需要的参数，profile 与数据库连接在 Django 初始化前由环境配置确定。"""
        if self.operation=='bootstrap_owner': parser.add_argument('login')
        if self.operation=='serve': parser.add_argument('--port', type=int, default=settings.NZ_PORT)
        if self.operation=='backup': parser.add_argument('destination')
        if self.operation=='maintain': parser.add_argument('--once', action='store_true')

    # 依据 operation 执行本地命令；凭据仅交互输入，服务清理与数据库校验均明确处理失败，不输出敏感驱动日志。
    def handle(self, *args, **options):
        """依据 operation 执行本地命令；凭据仅交互输入，服务清理与数据库校验均明确处理失败，不输出敏感驱动日志。"""
        service = Service(test_profile=settings.NZ_PROFILE=='synthetic')
        if self.operation=='bootstrap_owner':
            password = getpass.getpass('New Owner password (12+ characters): ')
            if password != getpass.getpass('Repeat password: '): raise CommandError('Passwords do not match')
            result = service.bootstrap_admin(options['login'], password)
            self.stdout.write('Store this MFA URI privately; it is shown once:')
            self.stdout.write(f"otpauth://totp/NZRevival:{quote(options['login'])}?secret={result['otp_secret']}&issuer=NZRevival")
        elif self.operation=='serve':
            from revival.server import local_server
            from nzproject.wsgi import application
            if not 1 <= options['port'] <= 65535: raise CommandError('Invalid port')
            settings.NZ_PORT = options['port']
            self.stdout.write(f"Local control plane: http://127.0.0.1:{options['port']}; profile={service.catalog['id']}")
            server = local_server(application, port=options['port'])
            try:
                server.run()
            except KeyboardInterrupt:
                pass
            finally:
                server.task_dispatcher.shutdown()
                server.close()
        elif self.operation=='maintain':
            from revival.rooms import maintain
            try:
                while True:
                    close_old_connections()
                    maintain(service)
                    if options['once']: break
                    time.sleep(5)
            except KeyboardInterrupt:
                pass
            finally:
                connections.close_all()
        elif self.operation=='backup':
            self.stdout.write(json.dumps(service.db.backup(options['destination'])))
        elif self.operation=='check_state':
            with service.db.transaction() as repository:
                connection = connections['default']
                with connection.cursor() as cursor:
                    # CHECK TABLE 在 MySQL 原生引擎执行；固定模型白名单不接受用户提供表名。
                    from revival.models import TABLE_MODELS
                    for table in TABLE_MODELS:
                        cursor.execute(f'CHECK TABLE `{table}`')
                        results = cursor.fetchall()
                        if not results or any(row[2]=='error' for row in results) or results[-1][3]!='OK':
                            raise CommandError(f'MySQL table check failed: {table}')
                    cursor.execute('SELECT VERSION()')
                    version = cursor.fetchone()[0]
                    cursor.execute('SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME LIKE %s', ['immutable_%'])
                    triggers = cursor.fetchone()[0]
                if triggers!=6: raise CommandError('Immutable history triggers are missing')
                self.stdout.write(json.dumps({'integrity':'ok','schema':repository.select('schema_version').fetchone()['version'],
                    'mysql_version':version,'immutable_triggers':triggers,'profile':service.catalog['id'],'retail_compatible':False}))
