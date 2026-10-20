"""阿里云触发邮件投递：固定 TLS 465、标准证书验证、不重试未知投递、凭据与正文不入日志。"""
import smtplib
import ssl
import re
from email.message import EmailMessage
from email.headerregistry import Address
from django.conf import settings

HOSTS = {'smtpdm.aliyun.com', 'smtpdm-ap-southeast-1.aliyuncs.com',
         'smtpdm-us-east-1.aliyuncs.com', 'smtpdm-eu-central-1.aliyuncs.com'}


class MailUnavailable(Exception):
    """仅向上层暴露固定错误码；服务商错误可能含收件地址或凭据，不回显底层异常。"""


def configured():
    """只有阿里云已核对区域、TLS 端口和全部凭据齐备才允许发送，不回退明文 SMTP。"""
    return (settings.NZ_MAIL_PROVIDER == 'aliyun-smtp' and settings.NZ_MAIL_HOST in HOSTS
            and settings.NZ_MAIL_PORT == 465 and bool(settings.NZ_MAIL_USERNAME)
            and bool(settings.NZ_MAIL_PASSWORD) and bool(settings.NZ_MAIL_FROM))


def send_code(email, code, purpose, *, operation=None):
    """单收件人投递：玩家码十分钟、管理员码五分钟；服务商接受不代表收件箱送达。"""
    if not configured():
        raise MailUnavailable('MAIL_NOT_CONFIGURED')
    label = {'register': '邮箱注册', 'reset': '找回密码', 'verify': '验证邮箱',
             'admin_login': '管理员登录', 'admin_step_up': '管理员高风险操作确认'}[purpose]
    message = EmailMessage()
    message['From'] = Address(settings.NZ_MAIL_SENDER_NAME, addr_spec=settings.NZ_MAIL_FROM)
    message['To'] = Address(addr_spec=email)
    message['Subject'] = f'NZ Revival {label}验证码'
    minutes = 5 if purpose.startswith('admin_') else 10
    context = ''
    if purpose == 'admin_step_up' and operation:
        # 只展示公共动作名和HMAC短指纹，不将新密码、玩家邮箱或任意业务正文放入邮件。
        action, confirmation = operation.get('action', ''), operation.get('confirmation', '')
        if re.fullmatch('[a-z][a-z0-9.]{1,63}', action) and re.fullmatch('[0-9a-f]{12}', confirmation):
            context = f'\n待确认操作：{action}\n操作确认指纹：{confirmation}'
    message.set_content(f'你正在进行 NZ Revival {label}。{context}\n验证码：{code}\n有效期为 {minutes} 分钟，仅可使用一次。'
                        '\n请勿将验证码转交他人。如非本人操作，请忽略本邮件。\n此地址不接收回复。')
    try:
        # 无调试输出，无自动重发。断线时结果可能未知，用户可在冷却后申请新验证码。
        with smtplib.SMTP_SSL(settings.NZ_MAIL_HOST, 465, timeout=12,
                              context=ssl.create_default_context()) as connection:
            connection.login(settings.NZ_MAIL_USERNAME, settings.NZ_MAIL_PASSWORD)
            rejected = connection.send_message(message, from_addr=settings.NZ_MAIL_FROM, to_addrs=[email])
            if rejected:
                raise MailUnavailable('MAIL_REJECTED')
    except MailUnavailable:
        raise
    except (OSError, smtplib.SMTPException, ValueError):
        raise MailUnavailable('MAIL_DELIVERY_FAILED') from None
