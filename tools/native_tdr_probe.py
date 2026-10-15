"""隔离运行当前 TDR 离线验证；仅专用 32 位进程，Job 生命周期与最长 30 秒限制。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

if __package__:
    from tools.native_job import JobGroup
else:
    from native_job import JobGroup


def main() -> int:
    """建立新私有证据目录后运行固定脚本，结束时只清理本次自有 Job。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify-research-response", action="store_true")
    parser.add_argument("--verify-zone-research-response", action="store_true")
    parser.add_argument("--verify-zone-notification", action="store_true")
    parser.add_argument("--verify-zone-login", action="store_true")
    parser.add_argument("--verify-zone-login-response", action="store_true")
    parser.add_argument("--verify-player-define-empty-response", action="store_true")
    args = parser.parse_args()
    # Zone 各专项模式绑定固定捕获样本，互斥且不能混入 GEO 响应构造路径。
    zone_modes = (args.verify_zone_notification, args.verify_zone_login, args.verify_zone_login_response,
                  args.verify_player_define_empty_response)
    if sum(zone_modes) > 1:
        parser.error("Select only one captured Zone request mode")
    if any(zone_modes) and (args.verify_research_response or args.verify_zone_research_response):
        parser.error("Zone request modes cannot construct GEO responses")
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.relative_to(root / "evidence" / "private")
    output.mkdir(parents=True, exist_ok=False)
    shell = Path(os.environ["WINDIR"]) / "SysWOW64/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(root / "tools/native_tdr_probe.ps1"), "-OutputDirectory", str(output.relative_to(root))]
    if args.verify_research_response:
        command.append("-VerifyResearchResponse")
    if args.verify_zone_research_response:
        command.append("-VerifyZoneResearchResponse")
    if args.verify_zone_notification:
        command.append("-VerifyZoneNotification")
    if args.verify_zone_login:
        command.append("-VerifyZoneLogin")
    if args.verify_zone_login_response:
        command.append("-VerifyZoneLoginResponse")
    if args.verify_player_define_empty_response:
        command.append("-VerifyPlayerDefineEmptyResponse")
    result = {"kind": "CURRENT_TDR_OFFLINE_JOB", "timeout": False, "game_started": False}
    with JobGroup() as job, (output / "worker.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        job.attach(process)
        try:
            result["exit_code"] = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            result["timeout"] = True
            job.terminate()
            result["exit_code"] = process.wait(timeout=5)
        finally:
            # 原生组件异常创建的后代也留在本 Job 内，检查后统一结束，不按名称匹配进程。
            remaining = job.pids()
            if remaining:
                job.terminate()
            result["owned_processes_before_cleanup"] = remaining
            result["exit_codes"] = job.collect_exits(timeout_ms=3000)
            result["cleanup_verified"] = not job.pids()
    worker_path = output / "worker.json"
    if worker_path.exists():
        result["worker"] = json.loads(worker_path.read_text(encoding="utf-8-sig"))
    result["passed"] = (result.get("exit_code") == 0 and result["cleanup_verified"]
                        and result.get("worker", {}).get("passed") is True)
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "timeout", "exit_code", "cleanup_verified")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
