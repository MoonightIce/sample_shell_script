# XCTest 配置与排障

## 当前 Mac 配置

依赖 Python 3、Node.js >=22.12、Xcode 与已配对且开启开发者模式的 iPhone。脚本默认：

- 设备：chengbinghui的iPhone；UDID 00008110-0018451C369B801E。
- 波点：com.wenyu.bodian；会话 bodian-iphone。
- daemon 状态：/private/tmp/bodian-agent-device-signed。
- team：45NG8MW7WK。
- Runner App：global.longbridge.agentdevice.bodian。
- Runner Test：global.longbridge.agentdevice.bodian.uitests。
- profile UUID：0f98c1ff-49ab-47a4-8926-00cf23fba8ca。
- signing identity SHA1：DEA0C91B502FDE203DA4084FE875F679A648D96E。

描述文件位于 /Users/admin/Library/Developer/Xcode/UserData/Provisioning Profiles/0f98c1ff-49ab-47a4-8926-00cf23fba8ca.mobileprovision；LongBridge_Development_Monkey_Test，曾核实包含目标设备，2027-08-27 到期。私钥和描述文件不随 Skill 分发；换 Mac、签名过期或设备变化时须重新核实，不能只复制这些标识。

## 命令

```sh
python3 scripts/device.py devices
python3 scripts/device.py open com.wenyu.bodian --relaunch --platform ios --device 'chengbinghui的iPhone'
python3 scripts/device.py snapshot -i
```

`--relaunch` 先结束再启动 App；`--device` 使用名称，不将 UDID 填入这个选项。运行设备命令需要访问 macOS 的设备服务，若 sandbox 返回 EPERM，使用获准的执行权限。

## 已修复的签名冲突

runtime/agent-device 是 agent-device 0.20.1 的独立副本，保留许可证。原工具在 dist/src/runner-disposal.js 强制 CODE_SIGN_STYLE=Automatic，但 Runner Xcode 项目指定了手动描述文件，导致 conflicting provisioning settings / xcodebuild code 65。副本改为 Manual；项目的 App 与 Test PRODUCT_BUNDLE_IDENTIFIER 改用 AGENT_DEVICE_IOS_RUNNER_APP_BUNDLE_ID 和 AGENT_DEVICE_IOS_RUNNER_TEST_BUNDLE_ID 构建变量，以匹配有效通配 profile。原全局 agent-device 与 Claude 配置没有改动。

已有 cbh Runner 的旧描述文件曾过期，安装报 0xe8008011；不要复用它。查看 daemon 日志与 xcodebuild 具体错误，不能把 code 65 直接解释成 UI 太复杂。确认正确 team、有效 profile、证书含对应私钥、profile 包含目标设备及 bundle 通配匹配，再启动本 Skill 的 Runner。

当前会话可以复用已运行 daemon。临时目录清理后，下次命令会使用 Skill 中保存的 Runner 源码重新构建/启动；这个冷 daemon 重建分支尚未作为 Skill 打包后的独立验收。
