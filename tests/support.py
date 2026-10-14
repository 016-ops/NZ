"""多线程测试使用真实独立连接，并在每个线程结束后关闭连接，防止测试库锁泄漏。"""
from concurrent.futures import ThreadPoolExecutor
from django.db import connections, close_old_connections


class DatabasePool(ThreadPoolExecutor):
    """在线程任务边界关闭 Django 连接，确保并发事务使用独立连接且测试后不泄漏。"""
    def submit(self, fn, /, *args, **kwargs):
        """包装任务进入独立连接边界；返回标准线程池 Future 供并发测试收集结果。"""
        def run():
            """执行原任务并在 finally 关闭线程连接；原任务返回值与异常原样传播。"""
            close_old_connections()
            try:
                return fn(*args, **kwargs)
            finally:
                connections.close_all()
        return super().submit(run)
