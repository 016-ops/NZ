"""旧TOTP/邀请审批DEV自动化流程已退役；不得自动读取私有凭据或恢复旧认证。"""
from tools.email_launcher import metadata


def command(client, token, action, **values):
    """保留普通受认证命令帮助函数；只使用调用方显式提供的当前会话。"""
    return client.request('/api/command', metadata(action=action, **values), token)


def owner_login(client, owner):
    """旧私有TOTP文件不能自动换取管理员会话；不读取传入字典内的凭据。"""
    raise RuntimeError('AUTH_METHOD_RETIRED: use password and fixed-mailbox email code through the admin portal')


def main():
    """在读取参数、配置、邮箱/SSH状态或建立连接前拒绝旧流程，要求人工走当前门户。"""
    raise RuntimeError('AUTH_METHOD_RETIRED: use the portal for email registration and administrator email authentication')


if __name__ == '__main__':
    main()
