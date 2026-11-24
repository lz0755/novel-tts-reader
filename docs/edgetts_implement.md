# 小说朗读室 · Edge TTS 接入与验收计划

修订日期为 2026 年 11 月 24 日，设计基线为 `lz0755/novel-tts-reader` 的 `main@44bf030`。本文是下一轮实施计划。仓库记录的 0.2.0 发布前回归为 99 项通过、1 项跳过，使用了带 Qwen 依赖的环境。Edge 的在线合成、发行包表现和延迟均须用本次交付版本取证。[基线提交](https://github.com/lz0755/novel-tts-reader/tree/44bf030236d769706ac1f976dca791fd88dd1bdb) · [既有验收记录](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/docs/STATUS.md#L22)

## 一、首版交付

读者在 Windows 或 Linux 上选择「Edge 在线语音」后，可以直接重听已有缓存。第一次遇到缺少音频的句子时，界面提供「允许本章发送到微软合成」和「仅用缓存」。完成本章预生成后，读者可以断网重听，并按原文顺序导出章节 WAV 和片段清单。

| 设计项 | 首版选择 |
| --- | --- |
| 依赖与运行方式 | 桌面程序提供 Edge 适配器。联网和 MP3 解码交给独立、可替换的轻量组件。Windows 与 Linux 核心版均可使用，读者无需安装 Python、FFmpeg 命令行、Qwen 或 CUDA。 |
| 播放方式 | 按句收全 MP3，转成 24 kHz、单声道、16 位 PCM WAV，校验后播放。沿用现有的预取、跳句、倍速和整章导出。 |
| 授权范围 | 默认仅用缓存。一次授权覆盖当前书、当前章、当前 Edge 来源，以及本章的播放、预取和预生成。切书、切章、切来源或重启后恢复仅用缓存。 |
| 音色和语速 | 提供内置中文预设、按角色绑定音色、自定义音色 ID 和手动刷新目录。语速沿用播放器的倍速控件。Edge 首版的音高和合成语速采用默认值。 |

## 二、从用户操作到缓存文件

### 授权和离线阅读

| 用户操作 | 程序行为 |
| --- | --- |
| 启动、恢复上次音源、打开书、同章翻页 | 从本地加载来源、绑定和音色目录。重启只恢复来源与偏好。同一章内翻页沿用本次授权。 |
| 播放一条已有缓存的句子 | 从磁盘校验并播放。即使断网、Edge 组件缺失或该音色已下架，仍能按原绑定找到有效缓存。 |
| 当前句缺少缓存，或用户点击「预生成本章」 | 在用户主动播放或预生成时显示缺段和授权入口。仅用缓存时，后台预取安静跳过缺段。授权提示明确正文将发送给微软，并说明范围包含本章后台预取。确认后才可提交缺段。 |
| 跳句、停止、撤权或切换章节 | 跳句和停止取消相关播放任务。撤权与切章同时作废旧授权、取消在途任务并清理等待队列。即使随后授权新章，旧章任务也无法借用新授权。已发请求的服务端处理状态记为未知。 |
| 导出章节 | 导出只读取并校验缓存。缓存完整时写出 WAV 和清单，缺段或损坏时提示先预生成，保留用户已有导出文件。 |
| 手动刷新音色目录 | 点击刷新即请求音色元数据，正文授权维持原状态。刷新失败沿用上次目录及内置预设。 |

现有 `Library.audio()` 会先调用 `prepare()` 再查缓存，翻页和导出也会读取 `capabilities()`。这两个方法在主程序本地完成。用户授权时，为当前书、章和来源创建专属的 Edge 适配器实例，后台任务持有当时的实例。切章先撤销旧实例的授权，再创建新实例。播放、预取、预生成和重试在发送正文前均核验各自持有的授权。[缓存入口](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/application/library.py#L37) · [现有章节切换](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/ui/window.py#L343) · [微软在线朗读的数据流](https://learn.microsoft.com/en-us/legal/microsoft-edge/privacy#read-aloud)

### 合成、取消和落盘

1. 主程序新增 `EdgeTTS`，实现 `TTSProvider` 的三个现有方法。来源固定为 `provider="edge_tts"`、`origin="cloud"`，首版设置 `can_stream=False`。本机运行的组件负责连接微软，它独立于只接受本地或模拟来源的 Qwen 运行时。[接口契约](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/ports/__init__.py#L21) · [本地运行时适配器](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/providers/local.py#L141)
2. 组件由主程序按需启动，使用标准输入输出交换任务、取消指令和结果，正文只经输入管道传递。诊断记录任务编号、错误类别和耗时。首版每次处理一个 Edge 合成任务。当前播放句在等待队列中优先，相同请求沿用现有缓存去重。
3. 组件按顺序收集同一句的音频块，确认传输完整后用 `miniaudio` 解码。下载和解码均设单段上限，可沿用现有云端路径的 32 MiB 与 600 秒作为首版上限。组件只写 `Library` 为该任务创建的唯一 `.partial` 文件。主程序用 `inspect_wav()` 验证声道、位宽、采样率、帧数和完整性，复核任务取消状态与书籍修订后，再原子替换并写入缓存索引。已有的同角色短句合并仅在播放时拼接已缓存片段。[云端音频边界](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/providers/cloud_tts.py#L21) · [落盘提交](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/application/library.py#L95) · [短句合并](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/application/batch.py#L40)
4. 对连接、读取和整个任务分别设定超时。正在连接、等待首包、接收中途或等待重试时，都能响应取消。关闭连接、释放文件句柄后清理临时文件，丢弃迟到结果。上游客户端返回 403 时提示服务不可用并结束任务。429 按服务返回的等待时间处理，超过任务期限就结束。对可重试的暂时性故障设置总尝试上限，计入上游客户端自身的重试。再次发送前复核授权和取消状态。切换到其他音源由用户主动选择。

### 音色、参数和缓存身份

- 内置普通话预设从晓晓 `zh-CN-XiaoxiaoNeural`、晓伊 `zh-CN-XiaoyiNeural`、云希 `zh-CN-YunxiNeural`、云健 `zh-CN-YunjianNeural` 起步。新合成以实际可用音色为准。目录保存稳定的 `ShortName`、语言区域和性别元数据，界面另显示中文名称。方言、粤语、台湾普通话及其他语言可通过刷新或手动输入补充。
- 按书籍、角色和来源模型保存绑定。旁白使用指定的旁白音色，有性别提示的角色从对应可用预设中分配，其余沿用默认选音规则。首次分配即保存，人工选择优先。目录刷新保留已保存的 ID。音色下架后旧句可重听，新句待用户明确选择替代音色后生成。[现有角色绑定](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/infrastructure/store.py#L485)
- 沿用 `SynthesisRequest.key`。正文、来源、模型及修订标识、音色 ID、实际生效的参数和音频处理版本决定缓存身份。对未公开修订号的远端模型使用明确标为别名的稳定标识。只修复连接的客户端升级、音色目录刷新、授权状态和播放倍速均可复用已有缓存。Edge 声明 `instruct=False`，保留书中的角色表演提示供其他音源使用，Edge 合成请求的有效风格为空。[请求指纹](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/domain/models.py#L90)

## 三、依赖、发行与使用条件

首轮验证锁定 `edge-tts==7.3.1`、`miniaudio==1.73` 和 Python 3.12，组件使用独立依赖锁文件，其 Python 库随组件放入可替换目录。上游 7.3.0 更新了服务连接兼容性，7.3.1 修复了与系统区域设置有关的日期解析。该版客户端请求 24 kHz、48 kbps、单声道 MP3。升级时用固定样本重跑合成、取消、缓存和双平台打包用例。[7.3.0 发布记录](https://github.com/rany2/edge-tts/releases/tag/7.3.0) · [7.3.1 发布记录](https://github.com/rany2/edge-tts/releases/tag/7.3.1) · [上游音频实现](https://github.com/rany2/edge-tts/blob/7.3.1/src/edge_tts/communicate.py) · [miniaudio 发行文件](https://pypi.org/project/miniaudio/1.73/#files)

Windows 与 Linux 核心版采用各自平台的轻量组件。两平台的本地 Qwen 完整版沿用相同桌面构建，并携带既有 0.6B 模型与独立运行时。主程序从本地读取组件兼容版本，缺失或版本不兼容时提示修复，阅读、缓存回放和其他可用音源仍可使用。[桌面打包脚本](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/scripts/build_desktop.py) · [现有发行流程](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/.github/workflows/package.yml)

| 发布前核对事项 | 需留存的材料 |
| --- | --- |
| 随包分发软件 | 按实际分发形式核对 `edge-tts` 的 LGPLv3 义务及解码器等传递依赖。包内附版权声明、适用的 GPL 和 LGPL 许可证全文，以及各依赖的许可文件。提供对应源码或合规获取方式，记录如何替换组件中的库，并在成品中验证替换可用。Nuitka 的 `--standalone` 描述发行目录布局，许可证判断以实际包含和加载的文件为准。[上游许可证](https://github.com/rany2/edge-tts/blob/7.3.1/LICENSE) · [仓库第三方清单](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/docs/third-party.md) |
| 使用微软在线语音 | 分别记录第三方桌面调用、批量预生成、持久缓存、导出音频再分发的适用服务条款、核对日期和已确认范围。界面用“在线、无需用户配置 API Key”描述接入方式。公开发行按已确认用途提供功能，待核实用途保留在验证记录中。需要明确商业使用条件时，另行评估有正式接口的 Azure Speech。[微软 Edge 朗读说明](https://learn.microsoft.com/en-us/legal/microsoft-edge/privacy#read-aloud) · [Azure Speech 官方接口](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech) |

## 四、实施里程碑

| 里程碑 | 主要工作 | 完成标志 |
| --- | --- | --- |
| 1 · 轻量组件 | 打通独立依赖、进程通信、取消和 MP3 解码，核对分发条件。 | 固定 MP3 样本可在两平台生成合规 WAV。记录依赖版本及平台，缺失组件时主程序可离线打开书库。 |
| 2 · 章节授权与缓存 | 接入 Edge 适配器、按章授权、任务队列、离线查缓存和导出。 | 旧章撤权后的新增正文请求为零，重复请求只生成一次，损坏或取消的任务不会产生成功缓存。 |
| 3 · 阅读器与角色音色 | 接入朗读来源菜单、内置及自定义音色、手动刷新、角色绑定和后台预取。 | 用含旁白和两名角色的样章，完成跨页播放、跳句、换音色、重启和断网回听。 |
| 4 · 双平台发行验收 | 从同一提交生成 Windows、Ubuntu 的核心版和各自的 Qwen 完整版，汇总实机记录与许可材料。 | 逐项登记下表结果，发行页注明各平台、各音源的实际验证状态，并附产物、SHA-256、提交和已知限制。 |

源码与打包入口如下。

- 新增 `src/novel_tts/providers/edge_tts.py` 及独立组件。主程序负责请求与授权，组件负责联网和解码。
- 在 `ui/voices.py` 和 `ui/window.py` 接入来源选择、恢复与撤权，在 `ui/player.py` 对接现有预取。在 `domain/voices.py` 和 `infrastructure/store.py` 接入按来源区分的预设与人工绑定。上述路径均相对于 `src/novel_tts/`。
- 在 `scripts/build_desktop.py` 和 `.github/workflows/package.yml` 加入轻量组件与许可材料，并通过 `scripts/package_qwen.py` 生成对应完整包。

实施时，每个里程碑将对应提交、测试记录和待测项同步到 GitHub Milestone。该阶段必需用例全部有通过证据后关闭里程碑。发行使用经过验收的同一提交打标签。Edge 的实测结果单独登记，既有 Qwen 云端、本地 GPU 及实机音频的待测项各自保留证据状态。[项目验收手册](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/docs/Novel_TTS_Milestones_Acceptance_v2.md#L172)

## 五、验收用例

| 环境及场景 | 可判定的通过条件 |
| --- | --- |
| 离线 CI · 音频完整性 | 假网络传输真实可解码的短 MP3 样本，输出为 24,000 Hz、单声道、16 位 PCM WAV，帧数与固定样本一致。空响应、截断及超限样本产生明确错误，成功索引和残留临时文件均为零。 |
| 离线 CI · 授权与后台任务 | A 章正在预取时切到 B 章，再授权 B。撤权生效后 A 章新增正文请求为零。重启、仅用缓存、浏览音色列表和导出期间，正文外发次数均为零。 |
| 离线 CI · 停止和异常 | 模拟迟迟无首包、传输中断、403、429 及等待重试时取消。任务能退出，重试有界。连续跳句 20 次后再释放延迟的响应，只有最后一次播放意图可发声，当前句和章节仍正确。 |
| 离线 CI · 角色、倍速与导出 | 人工绑定在重启和目录刷新后保持。由 1× 改为 1.5×、刷新目录、编辑 Edge 不支持的表演提示，额外合成次数均为零。仅改变某角色音色时重用其余角色缓存。完整章节的清单按原文顺序记录每段音色、`origin="cloud"` 和帧数。缺段时给出提示。 |
| Windows 11 x64 核心版、Ubuntu 24.04 x86_64 核心版 · 各自实机 | 在干净环境、普通用户、含中文及空格的路径下，无开发工具和独立显卡也可运行。用自写的公开测试短章完成显式授权、在线合成、真实设备试听、暂停、跳句和倍速。断网重开后可回听和导出，导出总帧数与片段之和一致。 |
| Windows、Ubuntu 的 Qwen 完整版 · 各自回归 | 在满足本地 Qwen 硬件要求的机器上断网启动，用随包 0.6B 模型合成一条从未缓存的句子并回放，清单来源为 `local`，Edge 正文请求为零。在同一本书切换回 Edge 后，两种来源的缓存可区分。缺少目标机器时记录待测平台。 |
| 真实网络 · 性能 | 在短句、长句上分别记录组件冷启动与已启动时，从点击播放到实际首声的耗时、整句生成耗时和音频时长。记录版本、环境、样本数及失败数。样本充分时报告 P50、P95。首版性能结论使用这些测量结果。 |

### 2026-11-24 工作树验收记录

在 Windows 11 x64（`10.0.26300`，RTX 4060 Laptop GPU，MSVC 14.5，Nuitka 4.3.5）及同机 WSL2 Ubuntu 24.04.5 LTS（内核 `6.18.58.1-microsoft-standard-WSL2`，直通 RTX 4060 Laptop GPU，GCC 13，Nuitka 4.3.5）上验证了基于 `6742d13` 的当前工作树。桌面、Edge 独立组件与 Qwen 完整包运行时均使用 CPython 3.12.16；组件锁定 `edge-tts==7.3.1`、`miniaudio==1.73`，Qwen 完整包锁定 `sidecar/uv.lock`（`torch==2.8.0+cu128`、`qwen-tts==0.1.1`、`faster-qwen3-tts==0.2.6`）与已校验的 `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`（`85e237c1`）。完整结构化取证见 [edge-release-20261124.json](evidence/edge-release-20261124.json)。

| 检查 | 本次结果 |
| --- | --- |
| 自动化回归 | Windows 离线默认配置 `pytest` 110 项通过、5 项跳过（0 失败；含联网样句、Qwen 依赖与 OpenSSL 的前次运行为 121 项通过、0 项跳过）；Ubuntu 24.04 `pytest` 111 项通过、4 项跳过（0 失败，`--smoke` 退出码 0）。新增 `scripts/package_qwen.py` 及其 4 个离线测试在 Windows 与 Ubuntu 均全部通过。[桌面与打包测试](../tests/test_edge_tts.py) · [组件单元测试](../edge_component/tests/test_worker.py) · [Qwen 完整包测试](../tests/test_package_qwen.py) |
| 授权与音色 | 从 Mock 直接授权 Edge 后，当前页立即加载 Edge 角色绑定。同章翻页保留授权。切章同时取消在途和排队的旧任务，切来源或重启恢复仅用缓存。播放与预生成的同意、拒绝，以及仅缓存预取均有回归测试。 |
| 合成与缓存 | 固定非静音 MP3 解码为 5,760 帧。已验证分块顺序、截断、下载及解码上限、403、429 等待和总尝试次数。相同句子的播放与预取合成一次。取消、损坏缓存和目录刷新失败均有回归测试。[固定音频样本](../fixtures/edge-tone.mp3) |
| 便携 Qwen 完整包机制 | 新增 [scripts/package_qwen.py](../scripts/package_qwen.py)，支持从核心版目录或压缩包结合 `sidecar/uv.lock` 与经 `novel-verified.json` 校验的 0.6B 权重构建双平台便携完整包；[worker.py](../sidecar/novel_tts_sidecar/worker.py) 支持相对模型路径解析，[local.py](../src/novel_tts/ui/local.py) 自动发现随包 `qwen_runtime/`，[.github/workflows/package.yml](../.github/workflows/package.yml) 已接入可选 `include_qwen` 打包入口。 |

#### 双平台四个发行包实测（含中文及空格安装路径）

| 发行包 | 体积 | SHA-256 | 实测结论 |
| --- | --- | --- | --- |
| `NovelTTSReader-windows-x86_64.zip`（Windows 核心版） | 73,523,258 B（~70.1 MiB） | `9c6b2c74c1a0c3a85643aa20623bebcf5215c5ad468ae159bd468ba6288603d1` | 中文空格目录 `--self-test` 通过（2 章 11 段 371,937 帧）；随包 GPLv3/LGPLv3 完整且验证替换 `edge_tts` 可用；真实联网合成 2 句后在 `扬声器 (Realtek(R) Audio)` 静音验证播放（首帧 228.2 ms）、暂停、续播、跳句和 1.5× 倍速；移除组件断网重开仅用缓存导出 108,288 帧（`segment_sum=108288`，`peak_pcm=18859`，`origin=["cloud"]`，组件启动 0 次）。 |
| `NovelTTSReader-windows-x86_64-qwen.zip`（Windows Qwen 0.6B 完整版） | 6,101,245,472 B（~5.68 GiB，Zip64，35,119 项） | `9fff6d764737dd671a83eca2759f73bd0c04f5abcd74c285f2d8b7b7f8bda795` | 中文空格目录 `--self-test` 通过并自动发现随包 `qwen_runtime/`；断网模式（`HF_HUB_OFFLINE=1`）加载随包 0.6B 模型（RTX 4060 Laptop，`faster-qwen3-tts` `int4`，加载+预热 26.709 s），未缓存句合成耗时 8.128 s（111,360 帧 / 4.64 s，`peak_pcm=19200`）；Realtek 静音播放、暂停、续播、1.5× 通过；导出清单 `origin=["local"]`，Edge 请求为 0；同书切回 Edge 验证缓存键隔离且缺段拒发，切回本地缓存仍可离线复用。 |
| `NovelTTSReader-linux-x86_64.tar.gz`（Ubuntu 24.04 核心版） | 128,876,833 B（~122.9 MiB） | `4e9849d5d33486779da2803cd163b5bc1a3a2f8e378b4b9a8557d9c088c3db00` | 中文空格目录 `--self-test` 通过（2 章 11 段 371,937 帧）；随包 GPLv3/LGPLv3 完整且验证替换 `edge_tts` 可用；真实联网合成 2 句后经 PulseAudio `RDP Sink` 静音验证播放（首帧 122.22 ms）、暂停、续播、跳句和 1.5× 倍速；移除组件断网重开仅用缓存导出 108,288 帧（`segment_sum=108288`，`peak_pcm=18859`，`origin=["cloud"]`，组件启动 0 次）。 |
| `NovelTTSReader-linux-x86_64-qwen.tar.gz`（Ubuntu 24.04 Qwen 0.6B 完整版） | 6,601,072,964 B（~6.15 GiB，39,640 项） | `be730540159002260cb101858156ef6cf91f0e1853ab3797925ad1dda43fd717` | 中文空格目录 `--self-test` 通过并自动发现随包 `qwen_runtime/`；断网模式加载随包 0.6B 模型（RTX 4060 Laptop，`faster-qwen3-tts` `int4`，加载+预热 23.626 s），未缓存句合成耗时 1.609 s（122,880 帧 / 5.12 s，`peak_pcm=23296`）；PulseAudio `RDP Sink` 静音播放、暂停、续播、1.5× 通过；导出清单 `origin=["local"]`，Edge 请求为 0；同书切回 Edge 验证缓存键隔离且缺段拒发，切回本地缓存仍可离线复用。 |

#### 真实网络 Edge TTS 冷启动 / 热启动耗时基准

测试环境：Windows 11 (`10.0.26300`)、Python `3.12.16`、`edge-tts 7.3.1`、`miniaudio 1.73`，音色 `zh-CN-XiaoxiaoNeural`，播放设备 `扬声器 (Realtek(R) Audio)`，总计 20 样本、0 失败。

| 场景 | 字数 | 样本 / 失败 | 整句合成 P50 / P95 (ms) | 点击播放到首声 P50 / P95 (ms) | 音频时长 P50 (s) |
| --- | --- | --- | --- | --- | --- |
| 组件冷启动 · 短句 | 7–8 字 | 5 / 0 | 4,141.54 / 6,955.35 | 4,816.13 / 8,283.89 | 2.14 |
| 组件已启动 · 短句 | 7–8 字 | 5 / 0 | 1,188.37 / 1,368.77 | 1,353.95 / 1,536.69 | 2.14 |
| 组件冷启动 · 长句 | 56–58 字 | 5 / 0 | 2,022.71 / 2,311.24 | 2,189.80 / 2,479.51 | 12.24 |
| 组件已启动 · 长句 | 56–58 字 | 5 / 0 | 1,383.56 / 3,900.49 | 1,550.08 / 4,163.50 | 12.24 |

首版性能结论：组件常驻热启动后，短句与长句点击播放到首声的 P50 分别为 1.35 s 与 1.55 s，长句（约 12.2 s 音频）合成耗时远低于播放时长，配合后台预取可满足连续朗读；冷启动首句包含子进程拉起与首次连接握手，受网络波动影响 P50 为 2.19–4.82 s。

复验时，先按 CI 配置同步桌面与组件的锁定依赖，然后运行 `uv run --no-sync pytest -q tests/test_edge_tts.py tests/test_package_qwen.py`。显式设置环境变量 `NOVEL_TTS_TEST_EDGE_ONLINE=1` 会启用真实联网用例，发送固定短句「窗前的风铃响了。」。

#### 剩余待测与发布前事项

- 无开发工具、无独显的**干净 Windows 11 x64 目标机**实机验证（本机已安装开发工具与独显）。
- 无开发工具的**原生 Ubuntu 24.04 x86_64 桌面物理机**实机与实体声卡验证（本次 Ubuntu 验证运行于 WSL2 24.04.5 LTS，音频设备为 WSLg PulseAudio `RDP Sink`）。
- 双平台真实扬声器 / 耳机的**人工试听**音质与倍速复核（本次自动化播放检查均为静音输出）。
- 将验收通过的最终提交推送并在 GitHub Actions 完成正式标签构建与 Release 发布。[当前发行工作流](../.github/workflows/package.yml)
- **公开分发服务条款确认**：对微软 Edge 在线朗读用于第三方桌面调用、持久缓存与导出音频再分发的适用条款做出正式放行结论。[第三方使用条件记录](third-party.md)


## 六、后续扩展

| 候选方向 | 启动条件与验证方式 |
| --- | --- |
| OpenAI 兼容语音接口 | Edge 首版验收后，按供应商逐项验证端点、鉴权方式、模型、音色、指令和响应格式。分别测试公有云及本机服务。现有配置只对 `custom` 允许空密钥，新增可信回环服务时单独定义凭据规则。无头 PCM 需明确采样率、声道和位宽。[配置实现](https://github.com/lz0755/novel-tts-reader/blob/44bf030236d769706ac1f976dca791fd88dd1bdb/src/novel_tts/infrastructure/profiles.py#L150) |
| 官方 Azure Speech | 对需要正式服务条款、配额或计费记录的使用场景单独接入。单列凭据、地区、预算和授权，实测官方接口的 24 kHz PCM 输出。 |
| Kokoro / Sherpa-ONNX | 在目标 CPU 上测模型及运行时的总下载体积、内存、中文多角色音质和生成耗时，再决定离线版规格。为该引擎单列能力和来源标识。[Sherpa-ONNX 模型文档](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html) |
| 流式 Edge 播放与其他引擎 | 真实长句首音等待影响阅读时，再验证有状态的增量 MP3 解码、跳句取消和缓存完整性。IndexTTS、CosyVoice、Windows 系统语音等按具体接口、授权、目标平台和真实样本独立评估。 |
