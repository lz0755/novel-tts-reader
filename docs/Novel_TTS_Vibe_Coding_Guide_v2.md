# 跨平台 AI 有声小说阅读器 Vibe Coding 指南

版本：v2.0  
日期：2026-11-21  
适用范围：Python 3.12 + PySide6 Qt Widgets；Windows 11 与 Ubuntu 24.04 x86_64；兼容 OpenAI 格式的小说处理 API；云端 TTS 与可选本地 Qwen TTS。

> 本文是开发执行指南。文中的目录、类名、脚本和命令属于拟定工程契约；当前交付不代表这些入口已经实现或测试通过。所有运行示例统一标记为“约定待实现入口”。实际依赖版本、API 模型 ID 和模型 revision，必须在对应里程碑验证后锁定。
>
> 与[项目计划书](./Novel_TTS_Project_Plan_v2.md)和[里程碑与验收手册](./Novel_TTS_Milestones_Acceptance_v2.md)配套使用。计划书决定范围与架构，验收文档决定通过条件，本文用于把工作拆成可交给编码助手的任务。发生冲突时，先说明冲突并核对用户已确认的要求，不擅自删除需求。

## 1. 开发顺序与工作方法

本项目先做出**完全离线、无 API Key、无 GPU、能实际播放 WAV 的完整 Mock 产品**，再接真实小说处理 API 和云端 TTS，完成 Windows/Linux 打包后，最后接入并验证本地 Qwen。每一步都沿用同一业务契约，避免把演示页面与正式实现分开维护。

| 阶段 | 主题 | 计划工作日 | 本阶段结束时应能证明什么 |
| --- | --- | ---: | --- |
| M0 | 骨架、契约、依赖与线程边界 | 2 | 纯核心可导入，接口和事件可校验，Mock 模式不依赖模型环境 |
| M1 | 完整 Mock 闭环 | 5 | 导入→分析→角色→合成→播放→导出，断网且无密钥可演示 |
| M2 | 文本、角色与纠音 | 6 | 原文可追溯，标注可修订，人工修改受保护，Unicode 定位正确 |
| M3 | 任务、缓存、播放与恢复 | 6 | 取消/重试/重启不污染新任务，缓存正确失效，播放有序 |
| M4 | 真实多供应商 LLM | 5 | OpenAI、DeepSeek 实际处理同一组小说样例，结构及原文覆盖验证通过 |
| M5 | 云端 TTS | 5 | 首先接通 Qwen 云端 HTTP，真实音频可缓存、播放、恢复和导出 |
| M6 | Windows/Linux 无 GPU 分发 | 5 | 两个平台的核心安装包在干净环境可启动和使用 |
| M7 | 最后接本地 Qwen 并实测 | 8 | 同一产品通过本地 sidecar 合成，0.6B 与 1.7B 路径有明确实测结论 |
| 缓冲 | 故障修复、跨平台差异与交付 | 6 | 补足已发现的具体风险，不用扩大需求消耗缓冲 |

工作量合计 42 个工作日，另留 6 个工作日缓冲，共 48 个工作日，约 10 周。这是排期估算，不是已完成进度或性能承诺。

### 每轮编码只交付一个可验收的变化

推荐每轮覆盖一个用户行为，例如“取消合成后重新开始，旧任务结果不能进入播放器”。任务描述同时给出：输入状态、预期行为、修改边界、失败场景和证据。不要只写“优化架构”“完善异常处理”。

单次任务流程：

1. 先读当前接口、相关模块和已有验证记录，明确本轮里程碑。
2. 列出准备修改的文件与原因；小任务直接执行，不反复请求已经授权的普通开发确认。
3. 实现最小完整变化，保持现有契约；需要改变契约时同步 schema、Mock、适配器和文档。
4. 运行与本轮风险直接相关的验证；记录命令、退出码和结果，避免只写“应该可用”。
5. 检查 diff 与用户体验，报告完成项、未验证项、下一任务。

## 2. 可复制的总控提示词

以下文本可作为编码会话的第一条任务，也可整理成项目内的 `AGENTS.md`。它是项目约定示例，不是要求创建或安装一个新的技能。

```text
你正在开发“小说多角色朗读桌面工具”。请先读取项目计划书、验收标准、
当前 milestone 状态和现有代码，然后执行本轮指定任务。

产品目标：导入小说，调用可配置的 OpenAI 兼容 API 标注章节、旁白、角色、
台词和风格；用户可以校对、绑定音色、纠音；选择云端或本地 TTS 后试听、
批量生成、断点恢复和导出。默认保留原文，不把模型改写当成原文。

固定技术与实施顺序：
1. Python 3.12 + PySide6 Qt Widgets；实际依赖版本验证后锁定。
2. 支持 Windows 11 和 Ubuntu 24.04 x86_64。
3. M0-M3 完成离线 Mock 产品；M4 实接 OpenAI 与 DeepSeek；
   M5 接云 TTS；M6 完成双平台核心打包；M7 最后验证本地 Qwen。
4. Mock/云端核心安装不得拉入 torch、qwen-tts 或 CUDA。
5. 默认入口可在无 API Key、断网、无 GPU 条件下以 Mock 模式启动。

线程与进程：
- Qt 主线程只处理 UI、QMediaPlayer/QAudioOutput 和轻量状态协调。
- 阻塞 HTTP、文本导入、音频转换放入有界 QThreadPool。
- SQLite 由专属 RepositoryWorker 线程串行访问，连接在该线程创建与关闭。
- 后台任务通过信号/不可变 DTO 回主线程，禁止直接访问 QWidget。
- 本地模型是可选 sidecar：QProcess 启动独立 Python 环境中的控制服务，
  FastAPI 管理协议与队列，单个 spawn 子进程持有 GPU/模型。
- 在 M7 前仅实现并测试 sidecar 协议 Mock，不导入真实模型或下载权重。

核心接口：
- LLMProvider.analyze_chunk(...) -> AnnotationResult
- TTSProvider.synthesize(...) -> AudioArtifact
- AudioArtifact 指向已完成且可校验的 WAV；来源必须标明
  mock_fixture / mock_tone / cloud / local。
- capabilities 与连接/能力 probe 独立；HTTP 200 不等于能力全部可用。
- ChatCompletionsAdapter 与 ResponsesAdapter 独立；Base URL、模型、
  协议、能力、凭据分开配置，不因某家“兼容 OpenAI”就假设音频 API 兼容。

正确性约束：
- canonical 原文位置使用 Python Unicode 码点半开区间 [start, end)。
- UI 高亮通过显式映射换算 Qt UTF-16 索引，选区不能切开 grapheme。
- 任何模型结果必须通过 schema、原文范围、顺序、覆盖和角色引用校验。
- 人工 override 优先，自动重分析不得覆盖人工修订。
- 所有任务携带 job_id、attempt_id、request_hash 和相关 revisions；
  播放另用 playback_epoch。接收结果时验证，过期结果不能修改当前状态。
- 缓存 key 包括真正改变生成音频的 effective 参数；播放器音量和播放倍速
  不改变原始合成缓存。修改朗读文本/模型/音色/合成语速等才按依赖失效。
- SQLite 与文件原子 rename 不构成跨系统原子事务，必须有恢复协议。
- 短句只允许按连续、同角色、同音色、同风格条件合并；没有精确边界时
  不承诺句内跳转，要求精确定位的模式回退为单句合成。
- 引导语裁切默认关闭，仅作为后续实验；不能让 CPU 对齐模型下载成为前置。
- 本地 0.6B-CustomVoice 不传 instruct；1.7B-CustomVoice 才按能力传入有效
  表演指令。两档保留相同业务入口，不把 1.7B 从首版选项中删除。

交付纪律：
- 不编造 API 参数、模型能力、已经运行的命令或测试结果。
- 不把 sleep 后返回成功当作 Mock 合成；必须生成或读取真实可播放 WAV。
- 不把下载过模型、成功 import 或生成一个文件当作本地 TTS 验收通过。
- 不在日志、截图、版本库或测试快照里写入 API Key 和完整私有小说。
- 不为了完成本轮任务擅自替换 UI 框架、引入数据库服务或增加 Agent 框架。
- 新增依赖说明用途、所属安装组、Windows/Linux 支持和打包影响。

本轮结束请输出：修改概述、可执行入口、验证命令与结果、未验证内容、
已知问题以及下一项最小任务。没有执行过的命令明确写“约定待实现入口”
或“尚未执行”；没有真实凭据/硬件的检查写“未验证”，不能写“通过”。
```

## 3. 建议目录与依赖边界

以下为建议布局；可随实际规模精简，但核心、UI、供应商适配和模型环境之间的方向不能反转。

| 路径 | 责任 | 边界 |
| --- | --- | --- |
| `src/novel_tts/domain/` | 小说、章节、片段、角色、音色绑定、修订号、任务状态 | 无 Qt、无 HTTP、无模型导入 |
| `src/novel_tts/application/` | 导入、分析、合成、播放计划、恢复等用例 | 依赖协议，不直接依赖具体供应商 |
| `src/novel_tts/ports/` | `LLMProvider`、`TTSProvider`、Repository 等协议 | 类型和行为契约可独立测试 |
| `src/novel_tts/providers/llm/` | Mock、Chat Completions、Responses | 供应商差异在这里收口 |
| `src/novel_tts/providers/tts/` | Mock、Qwen 云端、可选 OpenAI Realtime、本地代理 | 全部返回统一 AudioArtifact |
| `src/novel_tts/infrastructure/` | 文件、数据库、凭据、音频、日志、线程执行器 | 不放按钮事件或小说业务判断 |
| `src/novel_tts/ui/` | Qt Widgets、Presenter、表格模型、播放器控制 | 只依赖用例/DTO，禁止直接 SQL |
| `src/novel_tts/contracts/` | JSON Schema、sidecar 协议与版本常量 | Mock 与真实实现共用 |
| `sidecar/` | FastAPI 控制服务、队列、GPU 子进程与模型封装 | 独立环境和依赖锁，M7 才启用真实模型 |
| `fixtures/` | 自写小说样例、标注、故障响应、短音频 | 标明生成方式和用途，不能混充真实 TTS |
| `tests/` | 纯核心、契约、恢复、Qt 交互与集成验证 | 真实 API/硬件测试默认显式选择 |
| `scripts/` | 环境诊断、演示、打包、模型验证 | 启动参数清晰，无隐藏下载 |
| `docs/` | 决策、操作说明、版本矩阵、验收证据 | 状态需与实际产物一致 |

依赖按用途拆分：核心/桌面、云集成、开发验证、本地 sidecar。`qwen-tts`、PyTorch、CUDA 相关包只进入本地环境；即使用户不安装模型组件，导入 UI 模块、打开设置页和启动 Mock 也必须成功。云 TTS 的 SDK 同样按实际需要选择，不能为一个 HTTP 端点把一整套不必要服务依赖拉入主包。

Python 3.12 是项目目标运行时。M0 对 PySide6 和核心依赖做最小兼容验证并记录实际版本；M7 再对模型独立环境验证 Python/PyTorch/Qwen 组合。若模型环境有明确不兼容证据，保留桌面端目标版本，单独记录 sidecar 运行时调整，不静默改全项目版本。

### 线程实现要点

有界 `QThreadPool` 同时约束工作线程数和待提交作业数。长批量任务不应一次向线程池投递整本小说的全部片段；调度器按队列容量派发，暂停或取消后停止继续派发。网络调用设置有限超时，取消令牌在调用前后、重试等待和文件提交前检查。不能通过强杀 QThread 来取消阻塞请求。

RepositoryWorker 使用独立线程与命令队列。数据库连接不得在主线程创建后偷偷转给工作线程，也不能简单用 `check_same_thread=False` 掩盖并发设计问题。事务应短小，不在事务中等待 HTTP 或音频转换。返回值带请求关联 ID，UI 不持有 cursor、连接或可变 ORM session。

`QMediaPlayer` 和 `QAudioOutput` 保持在 Qt 主线程，通过信号接收播放器状态。窗口关闭时依次停止派发、取消等待、关闭播放器、持久化可恢复状态、退出数据库线程；sidecar 由清晰的停止/超时回收流程关闭，不能遗留后台 GPU 进程。

## 4. 先固定契约，再让 Mock 和真实实现接入

### 4.1 小说标注契约

`AnnotationResult` 至少应携带：文档/章节/分块 ID、canonical 文本版本、schema 与提示词版本、拥有的原文范围、角色候选、片段标注、置信/待确认标志、供应商和原始用量。字段精简程度可以调整，但不能失去追溯能力。

每个片段保存 `source_start`、`source_end`、类型、`character_id`、可选风格、人工确认状态。**用于朗读的原文由程序按范围切片取得**，不直接信任模型重新输出的台词。若给模型发送带 ID 的预切句子，返回的 ID 与细分范围也必须经本地校验。

分块上下文和分块拥有范围分开：相邻块可以看到上下文，但只能为自己的范围提交结果。聚合时检测遗漏、重复、乱序和跨章污染。角色别名合并需可回滚；未知说话人进入待确认列表，不能随机绑定一个已知角色。

### 4.2 音频契约

`AudioArtifact` 至少包含：稳定的 artifact ID、文件路径或存储 ID、SHA-256、容器/编码、采样率、声道、帧数/时长、来源类型、provider/model/voice 的有效绑定、request hash、生成尝试 ID、完成状态。它表示**已经完成且通过基础验证的 WAV**，不表示一个未来可能成功的任务。

云端返回 MP3、PCM 或临时 URL 时，由适配器和音频处理层取得真实字节、校验/解码并生成规范 WAV。裸 PCM 不能只改扩展名；转换时使用真实采样率，不重写文件头来假装完成重采样。任务进度和部分音频事件使用另一个事件类型，不能伪装成完成的 AudioArtifact。

统一接口是项目内部契约，不要求所有远端 API 返回同样的 JSON。`QwenCloudTTSAdapter`、`OpenAIRealtimeTTSAdapter` 和 `LocalQwenTTSAdapter` 分别处理自己的鉴权、会话、分块和错误。

### 4.3 capabilities 与 probe

静态配置描述“期望支持什么”；probe 记录“在何时、哪个模型和协议上实际验证了什么”。连接测试、结构化输出测试、流式终止测试、音色测试分开记录。供应商返回 200，但忽略 schema 或语速参数时，不能把该能力标为已验证。

## 5. Mock 场景：必须能播放，也必须能失败

Mock 的目的，是让业务行为能够重复验证。它无需模仿真实模型音质，但必须暴露真实系统会遇到的状态变化。

| 场景 | 固定输入/行为 | 验证目的 |
| --- | --- | --- |
| 基础旁白 | 两章短篇，自写文本；所有角色信息固定 | 完整导入与导出流程 |
| 双角色对话 | 同一章内旁白、甲、乙交替；存在“他说” | 说话人绑定与片段顺序 |
| 待确认台词 | 缺乏说话人线索，明确返回 unknown | 待确认 UI 与人工修订 |
| Unicode | 汉字、emoji、扩展字符、组合重音、换行、中文引号 | Python 码点与 Qt UTF-16 映射 |
| 跨块上下文 | 同一角色跨块出现，末句有上下文重叠 | 不重复、不漏读、不覆盖邻块 |
| 非法标注 | 空 JSON、全空格、截断 JSON、重叠范围、无效角色 | 拒绝错误结果与定向修复 |
| 延迟与乱序 | 片段 B 比 A 先完成；旧 attempt 晚到 | 有序播放与结果守卫 |
| 网络错误 | 401、429、503、读超时、连接中断 | 错误分类与重试预算 |
| 音频损坏 | 空文件、只有 WAV 头、错误采样率元数据 | 校验失败不能记成功 |
| 恢复 | 写临时 WAV 后中断、rename 后 DB 未提交即中断 | 恢复协议和孤儿文件处理 |
| sidecar | 启动慢、协议不匹配、忙、取消、进程退出 | M7 前完成本地服务接入逻辑 |

### Mock 音频要求

提供两类音频来源，并在 UI 和导出 manifest 中明确标注：

1. `mock_tone`：用标准库生成短促可听的 PCM/WAV，带平滑淡入淡出、保守振幅和片段间静音；可按角色使用不同音调，用来确认顺序与切换。不能将这种音调描述为小说语音。
2. `mock_fixture`：项目自制或可合法分发的少量固定朗读 WAV，用于试听控件与字幕体验。输入不匹配时不能假装朗读了当前文本，应回退音调并标明。

文件检查通过后，还要在有音频设备的 Windows/Linux 桌面实际试听一次。CI 的 WAV 解析检查和 headless Qt 冒烟不能证明扬声器已经播放。默认测试音量保守，启动应用不自动播放声音。

延迟使用可取消的调度或短步等待模拟，不在 UI 线程 `sleep`。故障由 scenario/seed 明确控制，不随机让回归测试时好时坏。

## 6. M0：骨架、契约与边界（2 天）

### 可复制任务提示词

```text
执行 M0，只建立小说朗读桌面工具的工程骨架与契约。
使用 Python 3.12 + PySide6 Qt Widgets，实际验证依赖后锁版本。
建立 domain/application/ports/providers/infrastructure/ui 边界，定义
AnnotationResult、AudioArtifact、ProviderCapabilities、JobEvent 等 DTO。
设计主线程、QThreadPool、RepositoryWorker 和可选 sidecar 的通信边界。

实现最小主窗口和 Mock 配置选择；任何模块导入均不要求 API Key、torch、
qwen-tts、CUDA 或模型文件。建立 sidecar 协议 schema，不接真实模型。
不要加入云账号系统、Web 前端、向量数据库、LangChain 或 Docker 运行前置。

交付目录、依赖锁、接口文档、最小启动入口、纯核心导入检查和版本记录。
尚未实现的入口请标注，不把目录占位视为功能完成。
```

### 交付与验收

交付最小桌面工程、契约定义、线程设计说明、依赖组和版本矩阵。纯核心导入不加载 Qt 或模型；桌面启动不调用外网；Qt 应用能打开并关闭；DTO 中的区间与版本字段有明确定义。Windows/Linux 至少完成环境差异检查，正式安装包验证留在 M6。

**约定待实现入口**：

```bash
python -m novel_tts --mode mock
python -m pytest tests/core tests/contracts
```

## 7. M1：完整 Mock 产品闭环（5 天）

### 可复制任务提示词

```text
执行 M1，在 M0 契约上完成完全离线的垂直闭环：
导入 TXT → 章节列表 → Mock 标注 → 角色音色绑定 → 合成队列 →
真实 WAV 播放 → 导出 WAV 与 manifest → 关闭后重新打开项目。

使用 QMediaPlayer + QAudioOutput，保留播放器对象生命周期。
MockTTS 必须返回经过验证的可播放 WAV，来源标为 mock_tone/mock_fixture。
不得只修改进度条、sleep 后成功、返回不存在的路径或在后台线程操作 QWidget。
导入和合成走有界工作线程，项目持久化走 RepositoryWorker。

先用两章、两角色、十余片段的固定小说样例完成操作体验。
同时提供一条失败后重试的确定性场景。无需真实 API、GPU 或模型下载。
报告实际点击流程、产生的文件与仍未实现的操作。
```

### 交付与验收

验收者应能在断网、无密钥环境中打开项目、导入短篇、修改音色绑定、点击生成、实际听到声音、暂停/继续、导出并重新打开。进度以已完成片段/总片段为依据，失败不能悄悄变成成功；导出的 manifest 必须标明 Mock 来源。

优先补齐这条流程，不提前花时间制作复杂皮肤、波形编辑器或实时声码器流式播放。基础按钮布局可以迭代，片段 ID 和音频契约从本阶段就应稳定。

**约定待实现入口**：

```bash
python -m novel_tts --mode mock --scenario basic_two_chapters
python -m pytest tests/mock tests/ui/test_mock_flow.py
```

## 8. M2：原文、角色、纠音与人工修订（6 天）

### 可复制任务提示词

```text
执行 M2，强化文本正确性，不接真实 LLM。
保留原始导入文件，建立版本化 canonical 文本；记录必要的清洗/换行映射。
内部全部使用 Unicode 码点半开区间；实现 Qt UTF-16 映射与 grapheme 安全边界。
用 emoji、组合字符、CRLF、空段落、中文引号和重复台词验证高亮与定位。

LLM Mock 返回 AnnotationResult；本地校验 schema、范围、连续覆盖、顺序、
重复和角色引用。正文从 canonical 切片取出，不能从模型生成字段替代。
分块上下文不拥有输出范围，防止相邻块重复朗读。

实现角色别名、unknown 待确认、人工更换说话人、纠音规则、撤销/重做或
明确版本回退。source_text 与 spoken_text 分开保存，纠音可预览差异。
人工 override 必须保留，重新分析只能提出建议，不能自动覆盖。
每次修改产生相应 revision，为 M3 的缓存失效和过期结果守卫提供依据。
```

### 交付与验收

同一段包含 emoji 的小说，在 Python 切片、数据库区间和 Qt 高亮中指向同一文本。不能用“中文通常一个字一个位置”替代映射。重复台词不能通过第一次字符串匹配来定位；整块原文的合法覆盖需由程序证明。

角色合并、音色绑定和纠音是不同修改：合并角色可能影响后续分配；音色变化只影响相关音频；纠音变化影响朗读文本，保留小说原文。自动分析重跑后，人工修改仍可见，冲突进入待处理列表。

模型提示词和输入样例将小说正文明确标为待分析数据；正文中的“忽略之前要求”等句子只是小说内容，不应改变输出 schema 或触发工具执行。

**约定待实现入口**：

```bash
python -m pytest tests/text tests/annotations tests/overrides
python -m novel_tts --mode mock --scenario unicode_and_manual_overrides
```

## 9. M3：任务、缓存、播放顺序与崩溃恢复（6 天）

### 可复制任务提示词

```text
执行 M3，以状态机和证据解决可靠性。
每个作业包含 job_id；每次实际尝试分配 attempt_id；每个请求有规范化后的
request_hash；输入带 document/annotation/voice/pronunciation 等 revisions。
播放队列另有 playback_epoch，用户切章/跳转/重建播放计划时递增。

任何后台结果进入数据库、UI 或播放器前都验证归属和 revision。
旧 attempt、旧文本和旧播放 epoch 的结果可留作历史，但不能覆盖当前状态。
实现有界队列、暂停后停止派发、合作取消、有限重试和中断恢复。

缓存只包含影响合成的 effective 参数：朗读文本、provider/model revision、
voice 绑定、实际风格/合成语速、实际生效的发音参数、输出处理版本等。
播放器音量、播放倍速、UI 排序和角色显示名不导致原始合成缓存失效。

设计临时文件→验证→同盘原子 rename→数据库完成状态的恢复协议。
承认文件系统与 SQLite 不是一个事务；注入各中断点，重启后核对并恢复。
短句只在连续且同角色/音色/风格下合并；精确定位模式无边界时回到单句。
```

### 交付与验收

重点看三条：

1. **取消后重试**：旧尝试晚到不会覆盖新尝试，不会突然开始播放。
2. **编辑后继续**：只有依赖变化的音频失效；播放器音量、倍速变化仍命中缓存。
3. **崩溃后恢复**：临时文件、已 rename 但未提交的文件、数据库记录缺文件分别有可解释处理，不能留下假成功。

保存顺序不是跨系统事务。建议用持久化 intent/staging 记录、内容 hash、临时文件、同卷 rename 和启动 reconciliation 建立恢复流程；数据库提交失败时保留可识别状态，避免立即删掉可能有效的音频。检查临时和最终文件的身份后再复用或清理。

修订号用于判断任务结果是否过期，不应把所有全局修订号直接塞进音频缓存 key。例如新增一条本句未命中的纠音规则后，实际朗读文本与合成参数没有变化，这句仍可以复用原缓存。适配器忽略的风格参数也不能伪装成有效合成参数；必须先确认并记录其能力和实际映射。

播放顺序由当前播放计划决定，合成完成顺序只表示“可用”。若 B 先生成而 A 未完成，应等待 A 或执行用户明确选择的跳过，不能自行播 B。预取队列有大小限制；达到暂停点不继续消耗整本小说的远端额度。

短句合并用于减少请求与改善连续性，不承诺它能消除所有接缝。没有可靠内部时间边界时，UI 显示整个合成组的播放进度；用户要求逐句跳转或精确高亮时，切回单句模式。引导语生成后裁切属于后续实验，默认关闭。

如果 Qt 逐文件切换造成明显空隙，优先在后台把已经独立生成的单句 PCM 拼接为播放批次，按累积帧数记录精确边界。该步骤保留单句缓存与不同角色音色，不等于把多人台词合并成一次模型推理；重新组装的播放批次需要新的播放映射。

本阶段还要启动无 GPU 的模拟本地控制服务，实际验证动态端口握手、临时令牌、健康检查、任务取消与进程回收；M0 只定义和校验协议，不承担真实模型环境准备。

**约定待实现入口**：

```bash
python -m pytest tests/jobs tests/cache tests/recovery tests/playback
python -m novel_tts --mode mock --scenario late_result_after_cancel
python -m novel_tts --mode mock --scenario crash_recovery
```

## 10. M4：真实 OpenAI、DeepSeek 与自定义 LLM（5 天）

### 可复制任务提示词

```text
执行 M4，将真实文本 API 接入已经可用的 Mock 产品。
实现 ChatCompletionsAdapter 与 ResponsesAdapter，统一返回 AnnotationResult。
配置项独立保存 provider、base_url、model、protocol、capabilities 和凭据引用。
模型 ID 可编辑；模型列表加载失败不能阻止手工填写。

在用户提供的真实凭据下分别验证 OpenAI 与 DeepSeek，对同一组小样例分析。
若当前无凭据，完成适配、HTTP 契约验证和操作说明，但真实集成记录写未验证。
按当前官方文档核实参数；不假设两个协议的 schema/流式事件相同。

结构化输出按能力选择 json_schema、json_object 或受控文本解析，所有路径
仍做本地业务校验。空白内容、拒绝、截断和错误 JSON 不得进入成功状态。
小说正文只用于标注，结果不能未经用户同意改写文本。

统一超时/退避/重试预算，协调 SDK 自带重试；记录 usage 与 request_id。
不把任意第三方 API 的 Idempotency-Key 当作远端 exactly-once 保证。
连接测试和 schema 能力测试分开；失败原因在设置页可读。
```

### 接入时的具体判断

OpenAI 推荐新项目使用 Responses，Chat Completions 仍然支持；多供应商接入可以同时提供两种适配器，不必强迫所有兼容服务迁移。DeepSeek 当前也支持 Responses，包括 `text.format`；但无状态以及部分参数静默忽略等差异仍需处理。不能写“DeepSeek 只能 JSON mode”，也不能写“协议兼容所以功能完全一致”。官方链接见文末。

配置中的 schema 能力与具体模型/协议绑定。JSON Schema 约束不是原文准确性的保证：角色是否存在、范围是否重叠、正文是否丢失仍由应用检查。JSON 修复只处理当前失败分块并有次数上限；不能失败就无预算地重复整本小说。

凭据优先保存到系统凭据服务；Linux 缺失可用服务时，提供仅本次会话使用/环境变量等明确路径，不悄悄降级为明文入库。应用只记录凭据引用和脱敏配置，错误日志不要回显 Authorization。

### 交付与验收

两个供应商各有一次真实成功样例及异常记录，字段包含验证日期、协议、模型 ID、有效参数、返回用量、是否流式、结果校验。自定义接口可通过固定 HTTP 假服务验证请求体与错误映射；未使用真实自定义服务时，不声称“兼容所有厂商”。

**约定待实现入口**：

```bash
python -m pytest tests/providers/llm tests/contracts/llm
python scripts/probe_llm.py --profile openai --fixture dialogue_short
python scripts/probe_llm.py --profile deepseek --fixture dialogue_short
```

## 11. M5：云端 TTS 与真实音频闭环（5 天）

### 可复制任务提示词

```text
执行 M5，首先实现 QwenCloudTTSAdapter 的云端 HTTP 分段合成。
选择并锁定一个当前文档明确支持的模型/地域/端点/音色组合，记录实测配置；
不要把 Qwen-Audio、Qwen3 Flash、Realtime 的请求体混用。
云端 TTS 使用独立配置与凭据，不能从 LLM 的 base_url 推导音频端点。

处理二进制、Base64 或临时 URL 等供应商返回形式，实际取得音频后转成
规范 WAV、校验并缓存，返回 AudioArtifact(origin="cloud")。
下载失败优先重试下载，不立即重复付费合成。记录请求 ID、用量和实际音频。
验证两角色、失败重试、播放暂停、缓存命中、改音色失效与导出。

OpenAIRealtimeTTSAdapter 作为可选项：先核实最新官方接口，验证忠实朗读，
处理音色会话限制、完成事件、音频格式和取消。不能只换一个模型字符串，
就把 Realtime 当 /audio/speech 使用；没有证据时不把它列为已完成能力。
```

### OpenAI 选项的当前边界

截至本文日期，OpenAI 已公告 `tts-1`、`tts-1-hd`、`gpt-4o-mini-tts` 等模型在 2027-01-06 关闭，并建议使用 Realtime API 的 `gpt-realtime-2.1-mini`。因此计划保留 OpenAI 云音频选项时，应设计专用 Realtime 适配器，而非把已弃用的 Speech 模型写成长期默认。[官方弃用表](https://developers.openai.com/api/docs/deprecations)

Realtime 当前文档说明：会话一旦输出过音频，就不能更改其 voice。多角色方案需按音色管理会话或采用受控的独立任务会话。小说文本朗读还要检查增删字、解释性前缀和改写；这些检查没有通过时，标为实验能力，不能因为能生成音频就用于批量有声书验收。[官方会话说明](https://developers.openai.com/api/docs/guides/realtime-conversations)

### 交付与验收

至少一种真实云 TTS 完成全链路。验收不仅检查文件存在，还要确认可解码、非空、时长合理、文本有被朗读、角色音色确实不同，以及暂停/续播与缓存使用正确。不同供应商的音色标识不能共享；切换供应商时显式重新映射角色音色。

计费统计保留供应商原始用量，再按有生效日期的价格配置估算。按字符、token 或时长计费的模型分别处理，不能用一个“每千字价格”覆盖所有模型。重试可能额外计费；客户端取消也不保证远端已停止执行。

**约定待实现入口**：

```bash
python -m pytest tests/providers/tts tests/contracts/tts
python scripts/probe_tts.py --profile qwen_cloud --fixture dialogue_short
python scripts/probe_tts.py --profile openai_realtime --fixture verbatim_reading
```

## 12. M6：Windows/Linux 无 GPU 打包（5 天）

### 可复制任务提示词

```text
执行 M6，交付不包含本地模型环境的 Windows 11 和 Ubuntu 24.04 x86_64 核心包。
在各目标平台构建并验证，不假设一个平台打包可直接跨平台运行。
锁定实际验证的 Python/PySide6/打包工具版本，说明必要系统依赖。

检查 Qt platform plugins、Multimedia、音频编解码/FFmpeg 依赖、字体、
缩放、文件对话框、路径编码、可写目录、凭据服务和退出清理。
代码/只读资源/用户数据/缓存/导出路径分离，不写安装目录。
使用 pathlib、Qt 标准路径或等价跨平台机制，禁止硬编码 C:\\ 路径。

在无开发环境、无 GPU、无 API Key 的干净环境跑 Mock 全流程；
在用户有凭据的环境验证云接口。Linux 同时记录实际会话类型与音频输出环境。
不要让用户安装 CUDA 才能打开云端版，也不要把本地模型权重塞进主安装包。
```

### 交付与验收

Windows 与 Ubuntu 各有安装/解压说明、启动入口、版本信息、依赖说明及真实截图/操作记录。验收路径包括中文目录、空格目录、只读安装位置、非管理员用户、无 GPU 启动、音频设备不可用时的错误提示。

Linux 的 X11/Wayland、音频服务和系统库差异不能由一条单元测试覆盖。至少完成目标 Ubuntu 桌面的实际播放与文件对话框测试，并说明本次确实验证的环境；不要把 Ubuntu 24.04 通过扩大表述为“所有 Linux 发行版通过”。

安装包仅负责核心应用。模型组件缺失时，本地 TTS 设置显示“未安装/未配置”及明确入口，其他页面正常工作。离线 Mock 启动不得触发依赖安装器、模型下载或云连接探测。

**约定待实现入口**：

```bash
python scripts/build_desktop.py --platform windows
python scripts/build_desktop.py --platform linux
python scripts/diagnose_runtime.py --mode core
```

以上两个构建命令分别在对应系统执行，`--platform` 只是拟定入口参数，不表示支持跨系统交叉打包。

## 13. M7：最后接入并验证本地 Qwen（8 天）

### 13.1 先替换协议 Mock，再加载模型

M0-M6 已经建立本地服务协议、任务映射、取消和进程退出处理。M7 的变化应主要发生在 sidecar 内部，将 Mock 合成执行器替换成真实 Qwen 执行器；主 UI 和小说处理流程无需知道 CUDA 细节。

```text
执行 M7，将既有 sidecar 协议 Mock 替换为本地 Qwen 实现。
QProcess 用参数列表启动独立模型环境的 Python，不使用 shell 拼接。
FastAPI 只负责控制、健康状态、能力、任务与音频传输；单个 spawn 子进程
持有模型与 GPU。有界队列、单 GPU 并发、明确的启动/加载/就绪/失败状态。
禁止 uvicorn reload 或多 worker 导致模型多份加载。

桌面通过 loopback 地址和会话凭据连接服务。Windows/Linux 均返回音频字节
或受控下载接口，不让桌面盲目信任远端绝对文件路径。
QProcess 停止、控制服务异常和 GPU 子进程异常有明确回收与重启策略。
如果使用 WSL2，它是单独配置的服务端部署路径，不假装 Windows 可直接
使用 Linux 虚拟环境的 python.exe 或共享同一套本机路径。

核对真实 Qwen 模型卡和 qwen-tts 接口，分别验证 0.6B 与 1.7B-CustomVoice。
接口架构兼容二者，能力按模型声明，不承诺 8GB 显存或实时性必然通过。
真实加载只在本阶段；无模型仍可用 Mock 和云端。
```

控制服务绑定 loopback，凭据不出现在命令日志和 URL 查询参数中；跨机部署不属于首版默认。健康检查应区分“服务正在响应”“GPU 子进程存在”“模型加载完成”“当前可以接单”，不能用一个 `ok=true` 表示全部状态。

本地模型明确使用 `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` 与 `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`，共用 `generate_custom_voice`。当前官方实现会忽略 0.6B 的 `instruct`，所以适配器对它声明不支持表演指令并省略该参数；1.7B 有有效风格时才传入。剧本风格仍保留，音频缓存使用实际生效参数。Base 的声音克隆与 VoiceDesign 属于其他模型能力，不在首轮必需范围。[官方推理实现](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)

官方高层接口返回完整波形与采样率；`non_streaming_mode=False` 不能直接当成音频分块流。首轮按段合成、提前生成下一段，仍使用已有完整 WAV 契约，不在本阶段同时重写播放器。[官方推理实现](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)

长推理运行时 HTTP 取消返回接受请求，不代表 GPU 已立刻中断。普通取消停止后续派发并阻止迟到插播；支持安全的合作取消时，在可检查边界退出。用户显式硬取消、关闭应用、控制服务退出，或者卡死/超时处置，可以在宽限期后回收 GPU 子进程，把活动尝试标为中断，确认资源释放后再按需加载和预热。停止播放与硬取消合成是不同操作，不能把两者绑定为同一语义。

### 13.2 性能与生成长度任务提示词

```text
为本地 Qwen 增加可配置、可测量的性能配置，不用传闻数值作为验收结果。
记录 OS、GPU/显存、驱动、Python、PyTorch/CUDA、qwen-tts 版本、模型 revision、
dtype、attention backend、实际生成参数、空闲/峰值显存和音频长度。

根据用户偏好，在支持条件满足的 Ampere 及以上 CUDA 环境中默认尝试开启
TF32 与 cuDNN benchmark，并提供关闭选项。检测版本与能力，记录最终有效值。
不要声称它们必定加速 BF16 路径；对照相同样例实测，并关注变长输入开销。
加载后执行 2-3 次代表性预热，预热结果单独记录，不混入稳定推理统计。

动态计算 max_new_tokens：以目标朗读时长、实测音频 token 率和余量估计，
每秒约 3 个汉字只能作为初始时长启发式。数字、英文、标点和情绪会改变时长。
设置上限/下限；触顶或尾句截断标为待重试/需拆分，不能以成功音频提交。
分别测短句、长句、对话、数字、人名和不同风格，并保留听感证据。

若 1.7B 在目标 8GB 设备 OOM，保留架构兼容，报告失败条件，尝试缩短片段、
减少并发和可支持优化，再比较 0.6B/云端路径。不要编造稳定性结果或自动
删除 1.7B 选项，也不要默认同时常驻两份模型。
```

`max_new_tokens` 控制的是模型生成序列预算，不能按文本 token 上限机械换算音频秒数。先确认所用模型/封装中这一参数的含义、默认值和停止条件，再根据实测样例校准。长文本先在安全边界拆分通常比盲目提高生成上限更可控，但要记录对连贯性和吞吐的影响。

FlashAttention 等优化是可选后端；在目标系统不可用时走官方实现实际支持的回退路径并记录。不把特定优化包编译成功设为整个桌面应用的运行前提。

### 13.3 交付与验收

至少完成：本地短句、双角色片段、较长段落；实际试听；0.6B 与 1.7B 的各自配置和结论；GPU 忙/内存不足/进程退出时的恢复；切回云端/Mock 后产品仍可用。测量并区分模型加载时间、首次生成、预热后生成、音频时长、实时率和峰值显存。

实时因子 RTF 统一定义为 `TTS 合成耗时 / 有效音频时长`，数值越小通常表示生成越快；模型加载、排队、文件转换和网络传输另外计入对应指标与总管线耗时。统计样本数与重复次数，不能拿一次最好成绩代表稳定性能。

若目标硬件或模型未提供，可交付接入代码和验证脚本，验收记录应明确写“该设备/该模型未验证”。最终计划是否完成以验收文档为准，不能把缺失硬件的本地阶段直接打勾。

**约定待实现入口**：

```bash
python scripts/diagnose_runtime.py --mode local-tts
python scripts/probe_local_tts.py --profile qwen_0_6b --fixture narration_short
python scripts/probe_local_tts.py --profile qwen_1_7b_customvoice --fixture dialogue_long
python scripts/benchmark_local_tts.py --warmup 3 --repeats 5 --profile qwen_1_7b_customvoice
```

这里的 profile 名是项目拟定别名；真正加载的模型仓库 ID、revision 和能力必须记录在配置与报告中，不能用别名掩盖换了模型。

## 14. 常见反例与纠偏提示词

| 常见偏离 | 为什么不合格 | 直接给编码助手的纠偏要求 |
| --- | --- | --- |
| “先装 CUDA，把模型跑起来再做界面” | 改变用户要求的顺序，阻塞无 GPU 开发 | 回到当前里程碑；恢复纯核心依赖；先完成协议 Mock |
| Mock 只返回 `success=true` | 无法验证解码、播放器、缓存和导出 | 生成可听 WAV，校验元数据，在播放器实际试听 |
| 一个 `main.py` 包含 UI/SQL/API/推理 | 难隔离线程、恢复和供应商差异 | 按既定端口拆开，先迁移当前用例，不重写全部功能 |
| 主线程调用 API 或等待进程 | 窗口失去响应，取消按钮无效 | 有界工作线程执行；用信号返回 DTO；设置超时 |
| `check_same_thread=False` 后各处直接写 DB | 没有解决连接和事务所有权 | 所有写入进入 RepositoryWorker，事务期间不做网络等待 |
| 模型返回全文后直接替换原文 | 无法保证无删改与定位一致 | 模型只提供标注，正文按 canonical 范围还原 |
| 用 Python 字符下标直接设 Qt 选区 | 非 BMP 字符和组合字符会错位 | 显式码点/UTF-16 映射，并保护 grapheme 边界 |
| 所有 HTTP 错误重试五次 | 错误密钥重复请求、费用难控制 | 按错误分类，协调 SDK 重试，保留总预算 |
| 把 `gpt-realtime-2.1-mini` 填进 speech 端点 | 模型与协议不匹配 | 使用专用 Realtime 适配器并验证会话与音频事件 |
| 把 Qwen 云端音色直接用于本地模型 | 音色 ID 和能力属于不同命名空间 | 按 provider/model 重新绑定并试听 |
| 取消后旧音频开始播放 | 缺少任务与播放计划守卫 | 同时校验 attempt/revisions/playback_epoch |
| 改播放器音量后整章重合成 | 把播放参数与生成参数混淆 | 缓存只依赖 effective 合成参数 |
| rename 成功就说事务完成 | DB 可能尚未提交或已经回滚 | 加持久化 intent 和启动 reconciliation，注入中断点 |
| 合并五句后按字数比例定位 | 伪造时间边界，用户跳转不准 | 无精确边界时组级显示，精确模式改用单句合成 |
| 默认下载对齐模型做引导语裁切 | 扩大首版依赖和验收范围 | 默认关闭，列为独立实验，不阻塞首版 |
| 看到 8GB 就承诺 1.7B 实时运行 | 忽略运行时显存与输入长度 | 按目标设备实测，报告条件和未通过项 |
| 一个平台打包成功就标跨平台 | 没验证系统插件、音频和路径差异 | 在两个目标系统分别构建和实际操作 |

## 15. 失败修复任务模板

将下面模板填好再交给编码助手。保留最小证据即可，不贴 API Key 或整本小说。

```text
当前里程碑：M__
当前任务：
期望行为：
实际行为：
最小复现步骤：
1.
2.
3.

环境：OS/架构、Python、PySide6、相关 SDK/模型版本。
输入：fixture 名或脱敏短片段、配置摘要、job_id/attempt_id/request_hash。
证据：错误类别、脱敏日志、退出码、相关产物/截图。
已经尝试的修复：

请先定位根因，再做范围最小的修复。优先检查契约、线程归属、状态转移、
revision 守卫与有效配置。不要通过扩大 timeout、吞掉异常、删除失败测试、
跳过校验或把失败标成成功来“修复”。
如果需要改变公共契约，列出影响并同步 Mock/真实适配器/文档。
修复后复现原问题并验证一个相关回归场景，报告执行证据与未验证部分。
```

不要因为某次供应商 400 就无条件删除所有可选字段并重发。先检查文档、有效 profile 和 capability，识别确切不支持字段，再做显式兼容处理。否则错误可能暂时消失，但 schema、风格或音色控制已经被静默丢弃。

## 16. PR 与阶段评审

每个 PR 说明具体问题、行为变化、验证证据和剩余限制。界面修改提供一张能说明问题的截图；恢复逻辑提供故障点和恢复结果；供应商集成提供脱敏的真实 probe 记录。不要求为排版、文案等可逆小改动编写映射实现的测试。

### 评审问题

- 当前变更是否仍属于本里程碑，是否偷偷提前安装模型或引入未约定框架？
- 核心包是否仍可在无密钥、无 GPU、无模型依赖的环境启动？
- UI 主线程是否出现阻塞调用，后台是否越权访问 QWidget/播放器/数据库连接？
- 模型输出是否经过结构与原文业务校验，人工 override 是否受保护？
- 结果写入是否校验 attempt、请求 hash 与修订号，播放是否校验 epoch？
- 缓存失效是否能解释，文件/DB 不一致时是否有恢复路径？
- 失败是否可见、可重试且有预算；取消是否停止新派发并隔离晚到结果？
- Provider 的真实能力是否有证据，模型名、字段、端点是否来自当前官方文档？
- 音频文件是否真的可播放，Mock/云端/本地来源是否清楚？
- Windows/Linux 的路径、打包、资源和音频差异是否有相应检查？

### PR 描述模板

```text
问题与影响
说明用户在什么操作下遇到什么具体问题。

本次变化
说明新增/修复的行为，以及为何采用这一实现。

验证
- 环境与命令：
- 实际结果：
- 必要的人工操作：

限制与后续
明确尚未真实验证的供应商、平台或模型，不使用“全部通过”概括未执行项。
```

## 17. 真实集成与本地验证证据

建议将每次正式 probe/验收记录保存为结构化 JSON 加简短 Markdown，便于比较，但不把任何真实密钥保存进去。

| 证据字段 | LLM | 云 TTS | 本地 Qwen |
| --- | --- | --- | --- |
| 日期、提交版本、配置版本 | 必填 | 必填 | 必填 |
| OS、Python、相关库版本 | 必填 | 必填 | 必填，另含驱动/CUDA/GPU |
| provider、协议、model/revision | 必填 | 必填 | 必填 |
| fixture ID 与输入 hash | 必填 | 必填 | 必填 |
| effective 参数与能力结果 | 必填 | 必填 | 必填 |
| request/job/attempt ID | 必填 | 必填 | 必填 |
| schema/原文覆盖/角色引用校验 | 必填 | 不适用 | 不适用 |
| 音频 hash、时长、采样率、来源 | 不适用 | 必填 | 必填 |
| 用量与成本估算口径 | 有返回则记录 | 有返回则记录 | 记录耗时/资源即可 |
| 试听/忠实朗读/尾句完整性 | 不适用 | 必填 | 必填 |
| 冷启动、预热次数、稳定耗时、峰值显存 | 不适用 | 可记录网络耗时 | 必填 |
| 状态与失败原因 | 必填 | 必填 | 必填 |

结果状态使用清晰的文字：`通过`、`失败`、`未验证`、`不适用`。例如“1.7B 模型成功下载、未在目标 8GB GPU 运行”只能是下载完成加运行未验证，不能记为本地推理通过。

参数中若某项被后端忽略，应分别记录“请求值”和“有效值/不可确认”。对 BF16、TF32、attention backend、语速和风格控制尤其如此。性能报告同时保留失败样例，避免只展示成功短句。

## 18. 每次交接必须留下什么

开发会话结束时更新一份简短状态文件：当前 M 编号、已完成用户行为、真实执行过的验证、未通过项、未验证项、依赖/契约变更和下一项最小任务。若工作中断，下一位编码助手应能够读这份文件继续，不必根据聊天记录猜测。

```text
当前阶段：M__
本轮完成：
可复现入口：
验证证据：
未通过项：
未验证项：
重要设计决定：
下一任务：
```

遇到新增需求，先判断它是否影响当前验收。例如“把皮肤做得更漂亮”通常不影响 M3 恢复流程；“允许同一角色跨章改名”会影响角色绑定和缓存，应登记依赖后处理。不能在一个任务中同时重做 UI、数据模型和供应商协议，以免失去可定位的回归边界。

## 19. 官方核对入口

以下链接供实施时核对事实；锁定版本时记录访问日期和实际采用的接口。文档更新可能改变模型、SDK 与端点，计划中的抽象接口保持稳定，供应商适配器随证据更新。

- [OpenAI Responses 迁移指南](https://developers.openai.com/api/docs/guides/migrate-to-responses)：协议、输入输出和流式事件差异。
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：schema 与 JSON mode 的边界。
- [DeepSeek Responses 兼容说明](https://api-docs.deepseek.com/guides/responses_api/)：能力、无状态特征与参数差异。
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)：JSON 提示要求、空白结果和截断处理。
- [Qwen-Audio-TTS HTTP](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-tts-http-api)：地域端点、输出格式和音频结果。
- [Qwen3-TTS 云端 API](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api)：另一模型/API 代际，勿与上项混用。
- [OpenAI 模型弃用表](https://developers.openai.com/api/docs/deprecations)：避免把已公布退役的模型设为长期默认。
- [OpenAI Realtime 会话](https://developers.openai.com/api/docs/guides/realtime-conversations)：音色、音频事件和会话限制。
- [OpenAI Python SDK](https://github.com/openai/openai-python)：安装版本、超时、重试和异常行为。
- [Qwen3-TTS 官方仓库](https://github.com/QwenLM/Qwen3-TTS)：模型、Python 接口与本地推理条件。
- [Qt for Python 文档](https://doc.qt.io/qtforpython-6/)：线程、Qt Widgets、Multimedia 与部署说明。

开发的首个可见成果应是能够完整操作并真实出声的 Mock 桌面应用；最终本地 Qwen 的结论应来自目标设备、真实模型和固定样例的测试记录。两者之间通过稳定的契约和逐阶段验收连接起来。
