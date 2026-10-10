#!/usr/bin/env python3
"""使用与领奖流程相同的 XCTest Runner 执行 agent-device 命令。"""
import subprocess
import sys
from run import ROOT, ENV, SESSION

if __name__ == "__main__":
    if len(sys.argv) == 1:
        raise SystemExit("用法：python3 scripts/device.py <agent-device 命令及参数>")
    command = ["node", str(ROOT / "runtime/agent-device/bin/agent-device.mjs"),
               *sys.argv[1:], "--session", SESSION, "--json"]
    raise SystemExit(subprocess.run(command, env=ENV).returncode)
