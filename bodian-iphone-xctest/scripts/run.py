#!/usr/bin/env python3
"""波点音乐真机流程；依赖本机配对、开发者模式和已验证的签名配置。"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
APP = "com.wenyu.bodian"
SESSION = "bodian-iphone"
ENV = dict(os.environ, AGENT_DEVICE_STATE_DIR="/private/tmp/bodian-agent-device-signed",
           AGENT_DEVICE_IOS_TEAM_ID="45NG8MW7WK",
           AGENT_DEVICE_IOS_BUNDLE_ID="global.longbridge.agentdevice.bodian",
           AGENT_DEVICE_IOS_RUNNER_TEST_BUNDLE_ID="global.longbridge.agentdevice.bodian.uitests",
           AGENT_DEVICE_IOS_PROVISIONING_PROFILE="0f98c1ff-49ab-47a4-8926-00cf23fba8ca",
           AGENT_DEVICE_IOS_SIGNING_IDENTITY="DEA0C91B502FDE203DA4084FE875F679A648D96E")


def clean(value):
    return "".join(c for c in value if unicodedata.category(c) != "Cf")


def call(*args):
    cmd = ["node", str(ROOT / "runtime/agent-device/bin/agent-device.mjs"), *map(str, args),
           "--session", SESSION, "--json"]
    proc = subprocess.run(cmd, env=ENV, capture_output=True, text=True, timeout=240)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(proc.stderr or proc.stdout or "工具未返回 JSON")
    if proc.returncode or not result.get("success"):
        raise RuntimeError(json.dumps(result.get("error", result), ensure_ascii=False))
    return result.get("data", {})


def snapshot():
    data = call("snapshot", "-i")
    nodes = data.get("nodes", [])
    if data.get("appBundleId") != APP:
        raise RuntimeError("前台应用发生变化，停止自动点击")
    for node in nodes:
        node["label"] = clean(node.get("label", ""))
    rect = nodes[0].get("rect", {}) if nodes else {}
    if (rect.get("width"), rect.get("height")) != (390, 844):
        raise RuntimeError("屏幕尺寸与已验证的 390×844 布局不同")
    return nodes


def labels(nodes):
    return "\n".join(n.get("label", "") for n in nodes)


def bounded(nodes, predicate):
    found = []
    for node in nodes:
        rect = node.get("rect", {})
        if (predicate(node.get("label", "")) or predicate(node.get("identifier", ""))) and 0 < rect.get("width", 0) < 390 and 0 < rect.get("height", 0) < 150 and 0 <= rect.get('y', -1) < 844 and 0 <= rect.get('x', -1) < 390:
            found.append(node)
    return min(found, key=lambda n: n["rect"]["width"] * n["rect"]["height"]) if found else None


def tap_node(node):
    r = node["rect"]
    tap(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)


def tap(x, y):
    call("click", x, y)  # 每步由后续状态读取验证，避免广告跳转卡住 settle。
    time.sleep(1)


def vip_days(nodes):
    if "福利会员剩余" not in labels(nodes):
        return None
    for node in nodes:
        match = re.fullmatch(r"(\d+)天", node["label"])
        if match and node.get("rect", {}).get("y", 844) < 280:
            return int(match[1])
    return None


def save_evidence(name, nodes=None):
    folder = ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    if nodes is not None:
        (folder / "snapshot.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=2))
    try:
        call("screenshot", folder / (name + ".png"))
    except Exception:
        pass
    return folder


def run(args):
    if args.inspect:
        nodes = snapshot()
        print(json.dumps({"vip_days": vip_days(nodes), "labels": [n["label"] for n in nodes if n["label"]]}, ensure_ascii=False, indent=2))
        return
    if args.target_days is None or args.target_days < 1:
        raise RuntimeError("必须提供正整数 --target-days")
    if not args.resume:
        call("open", APP, "--relaunch", "--platform", "ios", "--device", "chengbinghui的iPhone")
        time.sleep(2)
    completed = 0
    awaiting_reward = False
    landing_opened = False
    unknown_since = None
    for _ in range(args.max_ads * 12 + 20):
        nodes = snapshot()
        text = labels(nodes)
        days = vip_days(nodes)
        if days is not None:
            print(f"页面回读：福利会员剩余 {days} 天", flush=True)
            if days >= args.target_days:
                evidence = save_evidence('target-days', nodes)
                tap(30, 69)
                end = snapshot()
                if "畅享中" not in labels(end) or "福利会员剩余" in labels(end):
                    raise RuntimeError("已达到目标，但返回‘我的’页面未通过核实")
                (evidence / 'result.json').write_text(json.dumps({'target_days': args.target_days, 'observed_days': days, 'returned_to_my': True}, ensure_ascii=False, indent=2))
                call('screenshot', evidence / 'my.png')
                print(f"完成：{days} 天，已返回‘我的’页面。证据：{evidence}")
                return
            if completed >= args.max_ads:
                raise RuntimeError("已到本次广告轮数上限，目标尚未达到")
            next_button = bounded(nodes, lambda s: s in ("升级无损音质", "看3个视频立即续上VIP") or bool(re.fullmatch(r"再看\d+个立即续上VIP", s)))
            if not next_button:
                raise RuntimeError("未识别到已验证的领奖入口")
            tap_node(next_button)
            awaiting_reward = False
            landing_opened = False
            continue
        # 广告落地页：只关闭已验证的内置浏览器，不操作安装/购买按钮。
        exit_button = bounded(nodes, lambda s: s == "webview_exitbutton_in_bar")
        if not exit_button and any('ProductPageExtension.' in n.get('identifier', '') for n in nodes):
            exit_button = bounded(nodes, lambda s: s == '关闭')
        if exit_button:
            if not landing_opened:
                print("指定页面停留 15 秒", flush=True)
                time.sleep(15)
            tap_node(exit_button)
            landing_opened = True
            unknown_since = None
            continue
        if "点击退出关闭视频" in text:
            if "恭喜已获得奖励" in text:
                tap(362, 103)
                awaiting_reward = True
                continue
            if "立即下载" in text and not landing_opened:
                button = bounded(nodes, lambda s: s == "立即下载")
                if button:
                    tap_node(button)
                elif "进入详情页或第三方应用" in text and any(n["label"] in ("拼多多", "抖音") for n in nodes):
                    tap(195, 746)  # 两种广告模板已在真机截图验证。
                else:
                    raise RuntimeError("广告按钮布局未识别")
                continue
            if landing_opened:
                # 返回后最多等 20 秒，必须看到奖励成功提示再关广告。
                for _ in range(10):
                    time.sleep(2)
                    fresh = snapshot()
                    if "恭喜已获得奖励" in labels(fresh):
                        break
                else:
                    raise RuntimeError("返回广告后未出现奖励成功提示")
                continue
        if any(re.fullmatch(r'恭喜(?:获得|已解锁).*(?:VIP|会员|无损音质)', n['label']) for n in nodes) or re.search(r"再看\d+个(?:广告|视频)领第\d+日VIP", text):
            if awaiting_reward:
                completed += 1
                awaiting_reward = False
                print(f"已完成 {completed} 条广告", flush=True)
            more = bounded(nodes, lambda s: (s.startswith("再看1个") and "浏览" in s) or s in ("浏览5秒，马上领取", "看视频领取", "立即领取"))
            # 每天数奖励完成后回到主页面读数；中间奖励弹窗继续点击。
            remaining = re.search(r"再看(\d+)个(?:广告|视频)领第(\d+)日VIP", text)
            if more and remaining and int(remaining[1]) > 0 and completed < args.max_ads:
                tap_node(more)
                landing_opened = False
                awaiting_reward = True
                continue
            close = bounded(nodes, lambda s: s == '关闭')
            if close:
                tap_node(close)
            else:
                heading = bounded(nodes, lambda s: s.startswith(('恭喜获得', '恭喜已解锁')))
                if heading:
                    tap(354, heading['rect']['y'] - 30)
                else:
                    heading = bounded(nodes, lambda s: bool(re.fullmatch(r'再看\d+个(?:广告|视频)领第\d+日VIP', s)))
                    if not heading:
                        raise RuntimeError('奖励面板关闭位置未识别')
                    tap(354, heading['rect']['y'] - 39)
            continue
        if "私人推荐" in text:
            tap(333, 782)
            continue
        if "畅享中" in text and "福利会员剩余" not in text:
            status = bounded(nodes, lambda s: s == "畅享中")
            if status:
                tap_node(status)
                continue
        # 运营弹窗：AX 的“关闭”容器可能覆盖全屏；定位右侧小型关闭图标。
        if any(n['label'] == '关闭' for n in nodes):
            icons = [n for n in nodes if n.get('type') == 'Image' and not n['label']
                     and n.get('rect', {}).get('x', 0) >= 310
                     and 0 < n.get('rect', {}).get('width', 0) <= 50
                     and 0 < n.get('rect', {}).get('height', 0) <= 50
                     and 0 <= n.get('rect', {}).get('y', -1) < 700]
            if len(icons) == 1:
                tap_node(icons[0])
                unknown_since = None
                continue
            if {n['label'] for n in nodes if n['label']} <= {'波点音乐', '关闭'} and len(nodes) <= 5:
                tap(336, 255)  # 真机已确认的全图运营卡片（会员领取、生日促销）。
                unknown_since = None
                continue
        if unknown_since is None:
            unknown_since = time.monotonic()
        if time.monotonic() - unknown_since < 25:
            time.sleep(2)
            continue
        raise RuntimeError("遇到未验证页面，已停止。需根据截图补充规则")
    raise RuntimeError("状态切换超过上限，目标尚未达到")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="波点音乐：达到目标 VIP 天数后回到‘我的’")
    parser.add_argument("--target-days", type=int, default=7)
    parser.add_argument("--max-ads", type=int, default=30)
    parser.add_argument("--resume", action="store_true", help="从当前手机界面继续，不重启 App")
    parser.add_argument("--inspect", action="store_true", help="仅读取页面，不执行点击")
    args = parser.parse_args()
    if args.max_ads < 1:
        parser.error("--max-ads 必须为正整数")
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as error:
        path = save_evidence("stopped")
        print(f"已停止：{error}\n证据：{path}", flush=True)
        raise SystemExit(1)
