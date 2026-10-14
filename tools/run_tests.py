"""执行实际自动化测试并保存通过项、失败项和数据库版本；不将合成验证描述为真实游戏验收。"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


class Result(unittest.TextTestResult):
    """记录每个实际通过的 unittest ID，供证据报告区分执行数量与成功数量。"""
    def __init__(self,*args,**kwargs):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        super().__init__(*args,**kwargs)
        self.passed=[]
    def addSuccess(self,test):
        """保留 unittest 成功统计并追加测试 ID；失败和跳过不会被计入成功列表。"""
        super().addSuccess(test);self.passed.append(test.id())


def main():
    """解析 CLI 参数并执行测试，记录真实结果与版本；失败使用非零退出码。"""
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='evidence/stack-refactor-tests.json')
    parser.add_argument('--tooling',action='store_true',help='Run independent inventory tooling without a database')
    parser.add_argument('--labels',nargs='+',default=['tests'],help='Optional Django test labels for a targeted regression')
    parser.add_argument('--upstream',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=root/args.output;output.parent.mkdir(parents=True,exist_ok=True)
    if args.upstream:
        tests=['tests.test_account_db','tests.test_ap_auth','tests.test_purchase_atomicity','tests.test_room_registry_consistency']
        env=os.environ.copy()
        env['AF_ACCOUNT_DB']=str(root/'runtime/upstream-tests/accounts.db')
        (root/'runtime/upstream-tests').mkdir(parents=True,exist_ok=True)
        start=time.monotonic()
        result=subprocess.run([sys.executable,'-m','unittest',*tests,'-v'],cwd=root/'upstream/af-emulator',env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        output.with_suffix('.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        report={'kind':'UPSTREAM_UNIT_TESTS_NOT_CN_LIVE','command':tests,'exit_code':result.returncode,'seconds':round(time.monotonic()-start,3)}
    else:
        sys.path.insert(0,str(root))
        # 沙箱系统临时目录不可用时，测试仅在忽略的 runtime 缓存中创建自己的临时文件。
        import tempfile
        cache=root/'runtime/test-tmp';cache.mkdir(parents=True,exist_ok=True);tempfile.tempdir=str(cache)
        runner=None;old_config=None;mysql_version=None
        if args.tooling:
            suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_inventory')
        else:
            os.environ.setdefault('DJANGO_SETTINGS_MODULE','nzproject.settings')
            import django
            django.setup()
            from django.db import connections
            from django.test.runner import DiscoverRunner
            runner=DiscoverRunner(interactive=False,verbosity=0)
            runner.setup_test_environment()
            old_config=runner.setup_databases()
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT VERSION()');mysql_version=cursor.fetchone()[0]
            suite=runner.build_suite(args.labels)
        stream=io.StringIO();start=time.monotonic()
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
        if runner:
            runner.teardown_databases(old_config)
            runner.teardown_test_environment()
        output.with_suffix('.txt').write_text(stream.getvalue(),encoding='utf-8')
        report={'kind':'MYSQL_AUTOMATED_NOT_RETAIL_LIVE' if runner else 'INDEPENDENT_TOOLING','mysql_version':mysql_version,'tests':result.testsRun,'passed':result.passed,
                'failures':[{'test':t.id(),'traceback':trace}for t,trace in result.failures],
                'errors':[{'test':t.id(),'traceback':trace}for t,trace in result.errors],
                'skipped':[{'test':t.id(),'reason':reason}for t,reason in result.skipped],
                'exit_code':0 if result.wasSuccessful() else 1,'seconds':round(time.monotonic()-start,3)}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items()if k not in ('passed','failures','errors')},ensure_ascii=False))
    if not args.upstream and not result.wasSuccessful():
        print(json.dumps({'failing_tests':[t.id()for t,_ in result.failures+result.errors]}))
    raise SystemExit(report['exit_code'])


if __name__=='__main__':main()
