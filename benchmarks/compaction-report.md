# Coder 工具结果压缩实测

2026-11-27 使用 DeepSeek 的 Anthropic 兼容接口，对 Coder 已有的超长工具结果转存逻辑进行配对测量。测试使用 **8 条各 20,000 字符的合成工具输出**，覆盖 JSONL 清单、测试表格、构建日志、Markdown、CSV、中文报告、代码片段和重复字符。24 次请求均收到 HTTP 200，返回模型均为 deepseek-flash，每条回执有独立的完成 ID。

| 计量范围 | 原始返回 | 压缩后返回 | 降幅 |
| --- | ---: | ---: | ---: |
| 工具结果增量输入 Token | 54,185 | 5,679 | **89.5%** |
| 整个请求的输入 Token | 56,937 | 8,431 | **85.2%** |
| 每条工具结果字符数 | 20,000 | 2,078–2,088 | 约 89.6% |
| 原文归档逐字节恢复 | — | 8/8 | — |
| 原工具调用 ID 保留 | — | 8/8 | — |

## 测量口径

每个样本都发送原始工具结果、实际压缩结果和使用单个句点占位的空载请求。三个请求共享系统提示、工具定义、工具调用 ID 和会话结构。输入 Token 使用服务端回执中的 input_tokens、cache_read_input_tokens 与 cache_creation_input_tokens 三项之和。每个样本扣除各自的空载请求后，再按全部样本加总计算增量降幅。空载请求总计 **2,752** 个输入 Token。 [DeepSeek 用量字段说明](https://api-docs.deepseek.com/zh-cn/guides/anthropic_api/)

压缩调用直接使用项目的 Compactor.prepare。实验在调用前校验了原始工具结果与转存文件字节一致、头部验证码仍在预览内、尾部验证码只能在完整归档中读取，并确认工具调用 ID 保留。压缩结果中的路径采用合成相对目录，不包含本机位置。模型收到的任务只是返回头部验证码。原始组和压缩组的响应都找到了 **8/8** 个正确验证码，严格仅输出代码的格式分别达 **4/8** 和 **6/8**。该诊断仅覆盖头部可见信息，实验未比较长链路任务成功率或整体费用。

[24 条调用回执、逐样本用量及归档校验](deepseek-input-tokens.json) · [测量及复算脚本](measure_compaction.py)

## 复算与重跑

在 Coder 仓库根目录使用 Python 3.10+ 执行以下命令。第一条读取留档并复算汇总。第二条使用环境变量 DEEPSEEK_API_KEY 向官方 API 发起新的合成对照。

```bash
python benchmarks/measure_compaction.py --out benchmarks/deepseek-input-tokens.json --verify
python benchmarks/measure_compaction.py --out compaction-run.json
```
