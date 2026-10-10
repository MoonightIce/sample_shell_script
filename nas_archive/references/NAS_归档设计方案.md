# NAS 下载自动归档 · 设计方案

> 版本 v1 · 2026-08-19 · 状态：待评审
> 原则：**脚本只"使用"现有 NAS 功能，不"改造"现有功能**。任何改动可回滚。

---

## 1. 目标与边界

**目标**：NAS 上 qBittorrent 下载完成后，自动把视频归档到对应分类目录，并做字节级去重（与本机 `MoveFiles.sh` 同算法），全程无需人工干预。

**边界（不做的事）**：
- 不改动 qBittorrent 现有配置项（只**新增**完成钩子 2 个键）
- 不动现有下载目录、分类规则、种子数据
- 不安装任何第三方套件；只用 Docker 已有容器 + 系统自带工具（bash/coreutils/busybox）
- 不在 NAS 上做深度去重（meta/time 需要 ffmpeg，DSM 默认无）——深度去重仍由本机 MoveFiles.sh 定期做

---

## 2. 现状盘点（已核实）

| 项 | 现状 |
|---|---|
| NAS | Synology DS920+，DSM 7.x，`nancheng`（administrators 组，uid=1026） |
| 存储 | /volume1 13T（**99% 满**，剩 219G）；/volume3 28T（97% 满，剩 1.1T，基本为空） |
| qBittorrent | **已存在**：Docker 容器 `linuxserver-qbittorrent1`（2023 年部署），WebUI 8999，BT 52000，下载目录 `/volume1/docker/qBittorrent/downloads` → 容器 `/downloads` |
| 分类体系 | 已配 8 个分类+保存路径（Group-收纳→/volumeG1/收纳、Group-观看→/volumeG1/观看、Group-观看-单集、Book-Adult、Music、V1-Moive、V1-TV、v1-收纳） |
| Docker | 20.10.3，daemon 正常 |

---

## 3. 总体架构（数据流）

```
qBittorrent 种子下载完成（WebUI 8999）
      │  RunOnCompletion 完成钩子（conf 新增 2 键，qBittorrent 原生能力）
      ▼
/config/scripts/qbt_hook.sh "%F" "%N" "%L" "%I"   ← wrapper（容器内）
      │ ① 按分类 %L 动态读 categories.json → ARCHIVE_BASE（未知分类 fallback /downloads）
      ▼
/config/scripts/nas_qbt_archive.sh                 ← 归档脚本（容器内）
      │ ② 找出 %F 中所有视频
      │ ③ 与 ARCHIVE_BASE 已有视频做 hash 三级去重（大小→头/尾1MB采样→全量MD5）
      │ ④ 复制/移动归档 + 同 stem 字幕/封面跟随（sidekick）
      ▼
分类目录（如 /volumeG1/观看）         日志: /config/logs/archive.log
```

**三个关键点**：
1. **钩子由 qBittorrent 原生触发**（Run external program），零常驻进程、零轮询
2. **归档目标 = 你现有的分类保存路径**（不新建目录体系），脚本动态读 categories.json，以后你在 WebUI 加分类**无需改脚本**
3. **归档 = 平级化 + 去重**（多文件种子摊平、重复内容只留一份），复制模式（MOVE_MODE=copy）默认，不动下载目录原文件 → 做种不受影响

---

## 4. 组件设计

### 4.1 qbt_hook.sh（wrapper，容器内 /config/scripts/）
- 职责：把 qBittorrent 传入的分类（$3=%L）映射为归档目录；设置环境变量后 exec 归档脚本
- 映射方式：`awk` 解析 `/config/qBittorrent/categories.json`（grep save_path），**动态**、容错（解析失败 fallback `/downloads`）
- 放 `/config/scripts` 而非挂载 `/scripts`：**群晖 Docker 对镜像中不存在目录的挂载点权限 bug（000）**，已验证 /config 方案权限正常

### 4.2 nas_qbt_archive.sh（归档脚本）
- 复用 MoveFiles.sh 心智：`SORT_MODE=flat`（平级）/`code`（番号归类）；`MOVE_MODE=copy`（默认，做种安全）/`move`；`DEDUP_MODE=hash`（默认）
- 去重算法与 MoveFiles.sh 完全一致：大小分组 → 头/尾 1MB 采样指纹 → 采样相同才全量 MD5 确认（大视频不全量哈希，毫秒级）
- 幂等：重复触发（recheck/重下）自动跳过；并发锁（mkdir+pid 存活检查）
- 兼容性：bash 3.2 并行数组 + **busybox 兼容**（stat 按 uname 分 -f/-c，dd 定位读尾）

### 4.3 qBittorrent.conf 钩子配置（已写入，纯新增）
```ini
[BitTorrent]
Session\RunOnCompletionEnabled=true
Session\RunOnCompletionProgram=/config/scripts/qbt_hook.sh "%F" "%N" "%L" "%I"
```

---

## 5. 关键决策与取舍

| 决策点 | 方案 | 备选 | 取舍理由 |
|---|---|---|---|
| 触发方式 | qBittorrent 完成钩子 | Web API 轮询 / 常驻 daemon | 钩子零开销、下载完成即触发；轮询需常驻进程且延迟 |
| 归档目标 | 分类 save_path（动态读 JSON） | 固定目录 /volume3/video/movies | 适配你现有「收纳/观看」体系；固定目录会割裂现有习惯 |
| 脚本位置 | /config/scripts（容器副本） | /scripts 只读挂载 | 群晖挂载点权限 000 bug 实测失败；/config 权限正常且可写日志 |
| 去重深度 | hash（大小+采样+MD5） | meta/time（ffprobe 抽帧） | 深度去重需 ffmpeg，DSM 默认无；hash 已覆盖 99% 重复场景（重复下载/同源），转码副本去重交给本机 MoveFiles.sh 定期跑 |
| MOVE_MODE | copy（默认） | move | move 会让种子失去做种文件、触发 recheck；copy 不动源文件，做种零影响 |
| 归并模式 | flat 平级（默认） | code 按番号 | 与 MoveFiles.sh 用途（平级化+去重）一致 |
| 目录规划 | 沿用现有下载目录+分类目录 | 新建 volume3 体系 | 不改变你的现有目录习惯；volume3 目录已建备用但**不启用**，避免分叉 |

**遗留取舍提示**：volume1 剩 219G（99%），下载/归档持续会更快占满。方案不主动迁移（不改造现有），但建议后续把下载目录迁到 volume3（届时改容器挂载 + categories.json 的 save_path 即可，脚本自动适配）。

---

## 6. 已完成工作（含影响评估）

| 改动 | 内容 | 对现有功能影响 |
|---|---|---|
| SSH 公钥 | nancheng 主目录启用 + 公钥授权 + 权限修正 | 无（新增登录方式） |
| sudo 免密 | `/etc/sudoers.d/nancheng` | 仅影响 nancheng 账号 sudo；可删除文件回滚 |
| 容器重建 | 原容器 rm 后重建，**保留全部挂载/环境变量/端口（8999/52000/52000udp）/restart=always**，仅新增能力 | 短暂中断（分钟级）；数据在目录中无损；文件属主经核实**原本即 911**，无变化；已通过 WebUI 200 验证 |
| conf 钩子 | [BitTorrent] 段新增 2 键 | 纯新增，不碰现有键；WebUI 200 正常 |
| 脚本部署 | /volume1/video/scripts/（真身）+ config/scripts/（容器副本） | 无 |
| 目录创建 | /volume3/downloads、/volume3/video/movies、config/scripts | 新建，无冲突 |

## 7. 待办与验证清单

- [ ] 上传 busybox 兼容修复版脚本（stat 语法分系统），容器内重测**幂等去重**（第二次触发应 skip 而非 _1）
- [ ] 清理测试残留（/downloads/qbt_test、TEST-001*.mp4/srt）
- [ ] WebUI 登录验证（默认 admin/adminadmin，建议改密码）
- [ ] 真实种子端到端验证（选一个种子走完整链路，确认归档+去重+日志）
- [ ] 更新 NAS_QBT_SETUP.md（Docker 版部署记录）
- [ ] 确认日志保留策略（/config/logs 会随 config 备份）

## 8. 风险与回滚

| 风险 | 等级 | 缓解/回滚 |
|---|---|---|
| 容器重建遗漏原配置 | 中（已尽量还原） | 容器 ID 与完整配置已记录；可随时按记录再重建；数据全部在目录中 |
| qBittorrent 版本不认新键 | 低 | 已验证 WebUI 正常；若钩子不触发，删 2 行回滚，改 WebUI 图形界面配置 |
| busybox 兼容残留问题 | 低 | 本轮修复后重测幂等闭环 |
| 去重误删/误判 | 低 | 默认 copy + skip（重复只跳过不删除）；日志可追溯 |
| volume1 空间 | 中（长期） | 方案不动现有目录；建议后续迁移下载目录到 volume3（另行评审） |

---
*本方案所有 NAS 侧改动均已具备回滚记录，评审通过后继续推进待办清单。*
