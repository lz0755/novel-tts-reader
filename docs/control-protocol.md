# 本地控制接口 v1

父进程用 QProcess 和参数列表启动 Python。会话令牌从标准输入第一行传入，至少 32 字符。控制进程绑定 127.0.0.1 的端口 0，启动完成后在标准输出交接动态端口。所有后续 HTTP 请求使用 Authorization Bearer 头。关闭父进程写端触发控制服务退出与子进程回收。

合法握手示例为 `{"version":1,"host":"127.0.0.1","port":49152,"pid":1000}`。握手版本不匹配、非回环地址和未知握手字段均由 desktop contracts/handshake.json 拒绝。逻辑任务 ID 为 32 位小写十六进制文本。同 ID 相同输入返回原任务，输入冲突返回 409。

| 路径 | 方法与输入 | 响应 |
|---|---|---|
| /v1/health | GET | 控制进程状态、推理进程 PID、模型就绪、忙碌和接单状态 |
| /v1/capabilities | GET | 当前模型、版本、音色和实际能力 |
| /v1/models/load | POST，job_id、model，Qwen 可选 quantization（int4 或 none） | 先回收前一模型进程，再按此次请求或 runtime.json 加载，返回实际量化方式 |
| /v1/synthesis/jobs | POST，job_id、model、text、voice，可选 style | 受控单句作业；模拟 runner 可指定 scenario |
| /v1/jobs/{id} | GET | running、cancel_requested、cancelled、succeeded、failed 或 interrupted |
| /v1/jobs/{id}/cancel | POST，hard 为布尔值 | 软取消丢弃结果；硬取消等待进程终止 |
| /v1/artifacts/{id} | GET | 只传输已成功合成作业的完整 WAV |
| /v1/shutdown | POST | 停止接单并退出 |

例如未知字段返回 HTTP 400 和 `{"error":"invalid_request"}`，无效令牌返回 401，忙碌返回 409 和 `{"error":"busy"}`。软取消没有远端 GPU 即时停止保证。桌面 ProviderError 记录重试性与请求结果确定性。异常音频不发布，超时与进程退出记录为 interrupted。
