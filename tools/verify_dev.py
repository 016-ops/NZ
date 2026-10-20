"""旧TOTP/邀请审批DEV自动化流程已退役；不得自动读取私有凭据或恢复旧认证。"""
def main():
    """在读取参数、配置、邮箱/SSH状态或建立连接前拒绝旧流程，要求人工走当前门户。"""
    raise RuntimeError('AUTH_METHOD_RETIRED: use the portal for email registration and administrator email authentication')


if __name__ == '__main__':
    main()
