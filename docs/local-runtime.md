# 在小说朗读室使用本地 Qwen3-TTS

本机已经准备好 0.6B 和 1.7B 两档中文朗读模型。第一次使用时，在阅读器里选好推理用的 Python 和模型配置，再加载一档模型。之后即可离线朗读自己的 TXT，无需填写 API Key。

## 启动 Windows 阅读器

打开 PowerShell，运行下面四行。中间两行会按选择顺序打印两个文件的完整路径，稍后可复制到文件选择框。如果项目放在其他目录，先把第一行改成实际位置。

```powershell
Set-Location -LiteralPath "$env:USERPROFILE\PycharmProjects\novel-tts-reader"
(Resolve-Path -LiteralPath '.\sidecar\.venv\Scripts\python.exe').Path
(Resolve-Path -LiteralPath '.\build\qwen-models\runtime.json').Path
& .\.venv\Scripts\python.exe -m novel_tts
```

项目根目录的 `.venv` 启动阅读器窗口。`sidecar` 下的 `.venv` 负责在显卡上合成语音。如果提示找不到文件，先核对项目位置。首次安装请见[在新环境准备模型](#在新环境准备模型)。

## 首次选择配置并加载模型

1. 在阅读器顶部打开 **朗读来源 → 选择本地推理环境…**。
2. 第一个窗口「选取已安装 novel-tts-sidecar 的 Python」里，粘贴第一条输出的路径，结尾是 `sidecar\.venv\Scripts\python.exe`，点「打开」。
3. 第二个窗口里，粘贴第二条输出的路径，结尾是 `build\qwen-models\runtime.json`，点「打开」。底部会显示「本地运行时配置已保存」。两档模型共用这一个 JSON 文件。
4. **再次打开「朗读来源」并点「加载 0.6B-CustomVoice」**。需要给对白加语气指令时，点「加载 1.7B-CustomVoice」。等底部出现「本地模型已加载并预热」和模型名，再开始配音。本机 RTX 4060 Laptop GPU 在 BF16 权重下各冷启 5 次，从启动到完成预热的中位数为 0.6B 35 秒、1.7B 31 秒。

## 用自己的 TXT 试听

1. 点工具栏的 **导入 TXT**，选择小说，在「导入预览」确认书名、编码和分章后确定。想先试样书，可以直接点 **打开样书**。
2. 在左侧书架和目录中选书、选章，再单击正文中的一句。右侧「当前朗读片段」应显示 **本地语音**。在音色下拉框选 `vivian`，点 **保存角色音色**。这项选择对当前书、当前模型下的这个角色生效。
3. 点底部的 **播放**。首次合成该句时会显示「等待音频」。可以用「上一句」「下一句」跳转，用「停止」结束试听。
4. 如需控制对白语气，加载 1.7B，选中一段对白，打开 **校对 → 修改选区说话人 / 风格**，在「表演提示」填写 `低声而坚定` 并保存。右侧会显示提示词和「已应用」。0.6B 使用预设音色朗读，1.7B 支持这项表演提示。

## 连续听一章或导出 WAV

1. 设好音色后点 **预生成本章**。底部会显示「已处理 … / … 个片段」，等到「预生成本章完成」再点「播放」，即可从缓存连续收听。长章节先预生成，可以减少播放中途等下一句的时间。
2. 要保存整章，点 **导出章节**。在「为新导出文件夹命名」里选择保存位置，输入一个新的文件夹名，例如 `灯渡-第一章`。
3. 导出完成后，用播放器打开新文件夹中的 `chapter.wav`。同目录的 `manifest.json` 记录逐段使用的模型、音色和来源，本地合成的片段会标记 `"origin": "local"`。导出读取当前配音的缓存，改过模型、音色或朗读文本后，请再点一次「预生成本章」。

## 下次打开与切换模型

- **继续听已生成的内容**。在同一书库重开阅读器时，之前选择的两个路径会保留。如果上次用的是本地模型，可以直接重听缓存，也可从「朗读来源」选择「使用上次本地模型的缓存」。
- **合成新文本或切换模型**。直接在「朗读来源」点对应的「加载 …」菜单项。看到「缓存缺失，请先加载本地模型」时也用这一步。切换 0.6B 和 1.7B 共用已保存的路径，一次加载一档。
- **右侧仍显示 Mock 或云端语音**。回到「朗读来源」加载所需的本地模型，等预热完成，再回到正文确认右侧显示「本地语音」。
- **显示「本地运行时已退出，已缓存的段仍可播放」**。本机选中根目录的 `.venv\Scripts\python.exe` 时复现了该提示，这个环境只装了阅读器。重新选择 `sidecar\.venv\Scripts\python.exe` 和 `build\qwen-models\runtime.json`，再点一次「加载 …」。Windows 实机复验时，两档各出现 9 种音色，包括 `vivian` 和 `ryan`。若同一提示持续出现，可以用下文的命令行试音区分环境、模型文件和显卡问题。
- **更换推理环境或释放显存**。点「朗读来源 → 硬取消并卸载本地模型」可停止当前本地任务并释放模型。更换环境时，再选择新的 Python 和 `runtime.json`，然后重新加载。

## 切换 INT4 权重量化

本机的 0.6B 与 1.7B 共用 INT4 配置。在当前源码版阅读器打开 **朗读来源 → 本地权重量化 → INT4**。已选择本地模型时会自动重新加载，其他朗读来源下再点一次「加载 0.6B-CustomVoice」或「加载 1.7B-CustomVoice」。就绪提示末尾会显示 **INT4**。选 **关闭量化** 可恢复原始浮点权重，本机计算精度为 BF16。

**使用 runtime.json** 会在下次加载时读取配置文件。命令行和已发布的 Windows 预览包选用本项目更新后的推理环境，也能通过这个文件切换。在现有 JSON 的顶层设 `"quantization": "int4"`、`"dtype": "bfloat16"`，并保留 `models` 等条目。改回浮点权重时设 `"quantization": "none"`。旧配置省略该字段时会使用原始精度。已打开的旧预览包可先选「硬取消并卸载本地模型」，再加载所需模型。

两档各将语音生成器及其编码预测器的 **231 个 Transformer 线性层**按每组 32 个权重压成 INT4。激活、嵌入、输出头和音频编解码器使用 BF16。这条推理路径需要计算能力 8.0 或更高、支持 BF16 的 NVIDIA 显卡。加载时用已安装的 PyTorch CUDA 内核在内存中量化，可直接使用现有权重文件。每种精度单独缓存语音，切回原精度可以重听之前生成的音频。命令行试音可核对 `quantization`、`quantized_linear_layers` 和 `settings.int4_group_size`。 [PyTorch 2.8 INT4 CUDA 实现](https://github.com/pytorch/pytorch/blob/v2.8.0/aten/src/ATen/native/cuda/int4mm.cu)

RTX 4060 Laptop 8 GB 上，同一模型用相同音色、文本及采样种子各生成三段语音。显存取预热后 PyTorch 已分配量，速度取生成 1 秒音频的耗时中位数。两档的主要收益是节省显存。 [本机逐次实测与转写](evidence/qwen-int4-20261122.json)

| 模型 | BF16 → INT4 已分配显存 | BF16 → INT4 每秒音频的生成耗时 |
|---|---:|---:|
| 0.6B | 2.02 → 1.35 GiB | 2.23 → 2.27 秒 |
| 1.7B | 3.90 → 2.03 GiB | 2.26 → 2.31 秒 |

## 启用并确认推理加速

当前默认配置使用 `"dtype": "bfloat16"` 和 `"attention": "sdpa"` 进行计算，权重存储精度由 `quantization` 单独控制。预热和实际朗读都使用 `torch.inference_mode()`。

命令行试音会先输出模型信息。`requested_attention` 是配置值。如果 `attention` 显示 `sdpa_expanded_kv`，说明加载模型时，当前显卡在提示词处理和单步解码的探测中均选中了高效 SDPA 内核。如果显示 `sdpa`，模型使用 PyTorch 的默认内核选择。切换到新推理路径后，首次播放该句会生成新缓存。

在本机 RTX 4060 Laptop GPU 上，用 0.6B 和 `vivian` 朗读「雾散了。船靠岸了。」，预热后做了三组配对测试。每生成 1 秒语音的耗时中位数从 2.96 秒降到 2.38 秒，整句合成的耗时中位数从 10.58 秒降到 8.90 秒。

2026-11-22 在同机以 BF16 权重各做了 50 次未缓存合成。生成 1 秒语音的耗时中位数为 0.6B 3.02 秒、1.7B 2.57 秒。要连续听一章，可以先点 **预生成本章**，完成后再播放。 [本机 BF16 性能和显存实测](evidence/M7.md)

## 在新环境准备模型

本地推理需要支持 CUDA 的 NVIDIA 显卡和兼容驱动。新检出的项目先在根目录运行 `uv sync --locked --python 3.12` 安装阅读器。以下命令从项目根目录进入 `sidecar`，在独立的 Python 3.12 环境中安装 CUDA 12.8 版 PyTorch 和 Qwen-TTS，再准备需要的模型。首次下载需要网络。

```sh
cd sidecar
uv sync --locked --extra qwen --python 3.12
uv run --no-sync python -m novel_tts_sidecar.prepare --model 0.6B --root ../build/qwen-models
uv run --no-sync python -m novel_tts_sidecar.prepare --model 1.7B --root ../build/qwen-models
```

准备程序逐文件校验固定版本的模型，将模型目录的绝对路径和版本写入 `build/qwen-models/runtime.json`。如果以后移动权重，把准备命令的 `--root` 改为新的模型根目录，重新运行后在阅读器里选择该目录的 `runtime.json`。Linux x86_64 上，桌面程序使用 `.venv/bin/python`，文件选择框使用 `sidecar/.venv/bin/python`。

## 用命令行单独生成一句

在项目根目录的 PowerShell 运行下面的命令，会生成短句「晚钟敲响，桥上的灯亮了。」的 WAV。输出文件名须为新名称。

```powershell
& .\.venv\Scripts\python.exe .\scripts\probe_local_tts.py `
    --runtime .\sidecar\.venv\Scripts\python.exe `
    --settings .\build\qwen-models\runtime.json `
    --profile qwen_0_6b --fixture narration_short `
    --output .\build\qwen-short.wav
```

成功后，终端会输出 `frames` 和 `"origin": "local"`，可以直接打开生成的 WAV。本机运行时出现过 `SoX could not be found!` 提示，两档 12 Hz CustomVoice 均继续完成了合成。

要试带表演提示的对白，将 `--profile` 改为 `qwen_1_7b_customvoice`，`--fixture` 改为 `dialogue_long`，并更换输出文件名。

做性能对比时，`scripts/benchmark_local_tts.py` 使用相同的 `--runtime`、`--settings` 和 `--profile`，`--output` 指向新的 `.jsonl` 文件。它记录冷启动、未缓存合成、实时率和显存。`runtime.json` 中的 `tf32` 和 `benchmark` 可分别开关，`dtype` 和 `attention` 指定推理精度与注意力实现。实际设备首音和长时间连续播放的测量方法见[验收手册](Novel_TTS_Milestones_Acceptance_v2.md)。
