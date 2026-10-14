"""兼容旧命令入口；SQLite 文件参数被明确拒绝，不会悄悄切换数据源。"""
import argparse
import os
import sys


def main():
    """在 Django 初始化前选择 profile，再将旧命令名称映射到管理命令。"""
    parser = argparse.ArgumentParser(description='Local Django/MySQL NZ control plane')
    parser.add_argument('--db', help=argparse.SUPPRESS)
    parser.add_argument('--test-profile', action='store_true')
    parser.add_argument('command', choices=['bootstrap-owner','serve','backup','check','maintain','migrate','import-sqlite'])
    args, remaining = parser.parse_known_args()
    if args.db:
        parser.error('--db SQLite paths are no longer supported. Configure NZ_DB_* and use import-sqlite explicitly.')
    if args.test_profile:
        os.environ['NZ_PROFILE'] = 'synthetic'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nzproject.settings')
    from django.core.management import execute_from_command_line
    commands = {'bootstrap-owner':'bootstrap_owner','check':'check_state','import-sqlite':'import_sqlite'}
    execute_from_command_line([sys.argv[0], commands.get(args.command,args.command), *remaining])


if __name__ == '__main__':
    main()
