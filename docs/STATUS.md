# 交付状态

2026-11-21，源码版本 0.1.0。本轮交付可离线使用的内测包与 M0–M7 实现。原始计划、指南和 52 项条件保存在同目录。阶段标签记录当时可运行的提交，最终交付提交汇总后续修复与实测结果。

| 阶段 | Git 标签 | 已提交的实现与证据 |
|---|---|---|
| M0 | `m0-implementation` | [工程、版本契约、Qt 线程和离线启动](evidence/M0.md) |
| M1 | `m1-implementation` | [样书导入、多角色固定语音、真实播放器和导出](evidence/M1.md) |
| M2 | `m2-implementation` | [原文区间、角色、人工修订和三层纠音](evidence/M2.md) |
| M3 | `m3-implementation` | [并发任务、缓存、恢复及模拟进程故障](evidence/M3.md) |
| M4 | `m4-adapter` | [OpenAI、DeepSeek、自定义标注端点和费用记录](evidence/M4.md) |
| M5 | `m5-adapter` | [Qwen 云端语音、归一化、缓存与上传授权](evidence/M5.md) |
| M6 | `m6-build` | [Ubuntu 原生包、Windows 便携预览与虚拟音频回环](evidence/M6.md) |
| M7 | `m7-adapter` | [独立 Qwen 双模型运行时、下载核验与设备测量入口](evidence/M7.md) |

## 验证范围

- Ubuntu 24.04.5 x86_64 用户空间，普通账户从只读安装目录在 X11 虚拟显示器启动核心包。隔离网络后仍能导入样书、生成 11 段固定语音并导出 371,937 帧。另以 Qt 和 PulseAudio 虚拟输出验证播放、暂停、倍速和原句内续听。 [发行包记录](evidence/packaged.json) · [真实媒体回环](evidence/qt-loopback.json) · [续听测量](evidence/qt-resume.json)
- Windows x64 便携预览自带经摘要校验的 CPython 和原生 Windows wheels。Windows GUI 入口和样书自检已在 Wine 9 运行。Windows 11 原生干净机及 Windows Nuitka 构建待测。 [发行包记录](evidence/packaged.json)
- 46 个自动化测试全部通过。112 万 Unicode 码点样本完成 20 次随机跳转和重开定位。十章、350 个带原文锚点的标注样本与 50 项纠音样本已入库。十章角色归属还需要独立人工复核。 [自动化测试](evidence/automated-tests.json) · [长书实测](evidence/long-book.json) · [样本清单](../fixtures/manifest.json)
- 52 项中 26 项通过，3 项待测，23 项阻塞。实体声卡听感、真实云端模型与账单、Windows/Ubuntu 目标桌面、8 GB GPU 两档 Qwen 加载、显存及 30 分钟长播尚缺相应证据。各项的阻塞条件和运行入口见验收表。 [52 项验收记录](ACCEPTANCE.md)

## GitHub 同步

目标仓库由项目所有者指定。默认远端为 `lz0755/novel-tts-reader`，当前 GitHub 连接读取该地址返回 HTTP 404，创建仓库请求返回 HTTP 403，远端提交和 Milestones 尚未建立。完整 Git bundle 保留全部八个标签与提交历史。

`scripts/sync_milestones.py` 可以按阶段提交和标签推送并回读远端版本。当前交付的 `desktop-0.1.0-preview` 标签可在 GitHub Actions 触发 Windows 和 Ubuntu 原生构建并保留下载件。传入匹配的仓库名时，脚本还会根据 52 项实测结果更新 GitHub Milestones 描述和状态。阶段所有必需项通过后才会标记为完成。仓库地址如有变化先更新 origin。以下命令从已检出的项目目录执行。

```sh
python scripts/sync_milestones.py --remote origin --branch main --github lz0755/novel-tts-reader
```

检出完整交付历史可用 `git clone NovelTTSReader-milestones.bundle novel-tts-reader`。导出的源码 ZIP 适合直接阅读、运行和构建。
