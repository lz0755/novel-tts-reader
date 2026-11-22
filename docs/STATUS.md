# 交付状态

2026-11-22，源码版本 0.1.0。本次在 Windows 11 上复验既有内测包及本地 Qwen 工作区。阶段标签和发布包保留当时的实现版本。本次记录包含发布包摘要、运行时工作区基准提交与关键文件摘要。 [本次实机记录](evidence/windows-recheck-20261122.json)

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
- Windows 11 以普通账户运行已核验摘要的原生预览包。断网启动、系统 150% 缩放、v5 → v6 升级重装、Realtek 短时播放、双模型预生成与整章导出均取得实测记录。本机装有开发工具，干净目标机仍需验证。 [Windows 发行包复验](evidence/M6.md) · [Windows 实机截图](evidence/windows-reader-150.png)
- 预览标签交付时，本地 46 个自动化测试通过。主分支修复 Windows 管道等待和同一时间戳的费用记录排序，补充父进程关闭输入后的退出测试。GitHub Actions 中 Ubuntu 24.04 和 Windows Server 2022 各通过 47 项测试，桌面入口烟测均正常退出。 [预览标签测试](evidence/automated-tests.json) · [双平台复测](https://github.com/lz0755/novel-tts-reader/actions/runs/44915411279)
- 112 万 Unicode 码点样本完成 20 次随机跳转和重开定位。十章、350 个带原文锚点的标注样本与 50 项纠音样本已入库。十章角色归属还需要独立人工复核。 [长书实测](evidence/long-book.json) · [样本清单](../fixtures/manifest.json)
- 最新 52 项中 35 项通过、1 项未通过、4 项待测、12 项阻塞。本机源码回归 59 项通过。RTX 4060 Laptop 上两档均可生成新句，未缓存生成速度超过实时档时限。尚缺真实云端账号和账单、干净目标机、Ubuntu 原生桌面实体音频以及逐词人工评分。两档各自的 30 分钟边生成边播及缓存回放另约。 [52 项验收记录](ACCEPTANCE.md) · [本地模型复验](evidence/M7.md)

## GitHub 同步

11 月 22 日的复验结果记录在本地验收表。以下提交、阶段标签和 Milestones 对应预览版发布时的同步记录。 [本地验收表](ACCEPTANCE.md)

项目已同步到私有仓库 `lz0755/novel-tts-reader` 的 `main` 分支。远端保留 M0–M7 八个阶段标签与桌面预览标签 `desktop-0.1.0-preview`。八个 GitHub Milestones 分别列出对应验收编号、通过数及待测或阻塞数，可从各阶段进入证据记录。 [仓库](https://github.com/lz0755/novel-tts-reader) · [全部标签](https://github.com/lz0755/novel-tts-reader/tags) · [Milestones](https://github.com/lz0755/novel-tts-reader/milestones)

GitHub Actions 从预览标签分别在 Ubuntu 24.04 和 Windows Server 2022 构建桌面包。两个可执行程序都在中文安装目录下完成两章样书自检，导出第一章的 11 段音频，共 371,937 帧。发布页提供原生包与各自的 SHA-256。 [桌面预览版](https://github.com/lz0755/novel-tts-reader/releases/tag/desktop-0.1.0-preview) · [对应的构建与自检日志](https://github.com/lz0755/novel-tts-reader/actions/runs/44914455355)

`scripts/sync_milestones.py` 可以按阶段提交和标签推送并回读远端版本。当前交付的 `desktop-0.1.0-preview` 标签可在 GitHub Actions 触发 Windows 和 Ubuntu 原生构建并保留下载件。传入匹配的仓库名时，脚本还会根据 52 项实测结果更新 GitHub Milestones 描述和状态。阶段所有必需项通过后才会标记为完成。仓库地址如有变化先更新 origin。以下命令从已检出的项目目录执行。

```sh
python scripts/sync_milestones.py --remote origin --branch main --github lz0755/novel-tts-reader
```

检出完整交付历史可用 `git clone NovelTTSReader-milestones.bundle novel-tts-reader`。导出的源码 ZIP 适合直接阅读、运行和构建。
