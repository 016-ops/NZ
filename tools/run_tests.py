"""Run repeatable unittest suites and persist results without client data."""
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
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.passed=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.passed.append(test.id())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='evidence/automated-tests.json')
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
        suite=unittest.defaultTestLoader.discover(str(root/'tests'),top_level_dir=str(root))
        stream=io.StringIO();start=time.monotonic()
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
        output.with_suffix('.txt').write_text(stream.getvalue(),encoding='utf-8')
        report={'kind':'LOCAL_AUTOMATED_NOT_RETAIL_LIVE','tests':result.testsRun,'passed':result.passed,
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
