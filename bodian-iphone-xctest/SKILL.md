---
name: bodian-iphone-xctest
description: 使用 Mac 上的 agent-device/XCTest 控制 chengbinghui的iPhone，执行波点音乐福利会员领取、页面检查与故障恢复；达到指定剩余 VIP 天数后停止并返回“我的”。
---

# 波点音乐真机自动化

本 Skill 由 Mac 启动独立 AgentDeviceRunner，通过 XCTest 操作 iPhone。它不使用 iPhone 快捷指令，也不依赖手机上已有的 WDA。工具与脚本随 Skill 一起保存。

## 执行

以下路径相对本 Skill 目录；在 shell 中使用解析后的绝对路径。

- 领取至用户指定天数（未指定时沿用 7 天）：`python3 scripts/run.py --target-days 7`。
- 从当前广告或奖励界面继续：增加 `--resume`。
- 仅读取当前波点界面：`python3 scripts/run.py --inspect`，需已有 active session；若返回 SESSION_NOT_FOUND，先用 device.py 的 open 建立会话，再读取。
- 每次最多 30 条广告，可用 `--max-ads` 改变上限。Ctrl+C 可停止。

只运行一个控制进程。设备必须是 `chengbinghui的iPhone`，已连接、配对、解锁并开启开发者模式；不要切换到另一台 iPhone。初次启动或连接失败时，先读 [XCTest 配置与排障](references/xctest.md)。

## 判断与恢复

脚本按当前界面状态选择操作，关闭运营弹窗，继续奖励弹窗；进入指定页面后停留 15 秒。只以福利会员主页面“福利会员剩余”的实际天数判断达标，广告成功文案和“+1天”不作为到账依据。达标后回到“我的”并验证。

遇到未识别页面、前台 App 改变、屏幕尺寸变化、工具错误或奖励超时会停止。检查 `runs/` 下的截图与结果，再根据真实页面修改规则；不要连续试点、点击安装或购买。已有授权继续有效，但不要将一次运行变成未经请求的定时任务。

需要查看、截图或按真实 UI 调整规则时，用 `scripts/device.py` 执行相同 Runner 的命令：

```sh
python3 scripts/device.py snapshot -i
python3 scripts/device.py screenshot /absolute/path/current.png
python3 scripts/device.py click 333 782
python3 scripts/device.py --help
```

坐标以点为单位；现有规则仅验证过 390×844 点。先读取页面和截图再操作。`关闭` 的可访问性容器可能覆盖整个屏幕，不能盲点容器中心；网页关闭按钮的 `webview_exitbutton_in_bar` 位于 identifier 字段。详细分支及验证边界见 [流程与验收](references/workflow.md)。

交付时报告实际回读天数、是否返回“我的”、证据目录与停止原因。Skill 结构验证、脚本运行与真机完整流程验证分别说明。
