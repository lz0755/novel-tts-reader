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
4. **再次打开「朗读来源」并点「加载 0.6B-CustomVoice」**。需要给对白加语气指令时，点「加载 1.7B-CustomVoice」。等底部出现「本地模型已加载并预热」和模型名，再开始配音。本机 RTX 4060 Laptop GPU 的两次试验中，从启动推理进程到完成预热约需 49～55 秒。

## 用自己的 TXT 试听

1. 点工具栏的 **导入 TXT**，选择小说，在「导入预览」确认书名、编码和分章后确定。想先试样书，可以直接点 **打开样书**。
2. 在左侧书架和目录中选书、选章，再单击正文中的一句。右侧「当前朗读片段」应显示 **本地语音**。在音色下拉框选 `vivian`，点 **保存角色音色**。这项选择对当前书、当前模型下的这个角色生效。
3. 点底部的 **播放**。首次合成该句时会显示「等待音频」。可以用「上一句」「下一句」跳转，用「停止」结束试听。
4. 如需控制对白语气，加载 1.7B，选中一段对白，打开 **校对 → 修改选区说话人 / 风格**，在「表演提示」填写 `低声而坚定` 并保存。右侧会显示提示词和「已应用」。0.6B 使用预设音色朗读，1.7B 支持这项表演提示。

## 连续听一章或导出 WAV

1. 设好音色后点 **预生成本章**。底部会显示「已处理 … / … 个片段」，等到「预生成本章完成」再点「播放」，即可从缓存连续收听。在这台机器的三段试音中，生成 1 秒语音用了约 3.6～6.8 秒，长章节适合先预生成。
2. 要保存整章，点 **导出章节**。在「为新导出文件夹命名」里选择保存位置，输入一个新的文件夹名，例如 `灯渡-第一章`。
3. 导出完成后，用播放器打开新文件夹中的 `chapter.wav`。同目录的 `manifest.json` 记录逐段使用的模型、音色和来源，本地合成的片段会标记 `"origin": "local"`。导出读取当前配音的缓存，改过模型、音色或朗读文本后，请再点一次「预生成本章」。

## 下次打开与切换模型

- **继续听已生成的内容**。在同一书库重开阅读器时，之前选择的两个路径会保留。如果上次用的是本地模型，可以直接重听缓存，也可从「朗读来源」选择「使用上次本地模型的缓存」。
- **合成新文本或切换模型**。直接在「朗读来源」点对应的「加载 …」菜单项。看到「缓存缺失，请先加载本地模型」时也用这一步。切换 0.6B 和 1.7B 共用已保存的路径，一次加载一档。
- **右侧仍显示 Mock 或云端语音**。回到「朗读来源」加载所需的本地模型，等预热完成，再回到正文确认右侧显示「本地语音」。
- **更换推理环境或释放显存**。点「朗读来源 → 硬取消并卸载本地模型」可停止当前本地任务并释放模型。更换环境时，再选择新的 Python 和 `runtime.json`，然后重新加载。

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
