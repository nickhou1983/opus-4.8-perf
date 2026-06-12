# TTFT Benchmark

用于测量 Claude 模型首个 Token 响应时间（TTFT, Time To First Token）的基准测试脚本。

TTFT 指从发出请求到收到第一个有效增量事件的耗时。脚本基于流式 API 逐事件读取，默认将首个 text_delta 或 thinking_delta 作为 TTFT 触发点，并同时记录整次请求总耗时与输出 token 数。

## 功能概览

- 基于流式事件精确测量 TTFT
- 支持预热轮与正式测量轮
- 输出统计指标：min/max/mean/median/p90/p95/p99/stdev
- 支持 adaptive thinking 与 effort（low、medium、high）
- 支持 TTFT 口径切换：any 或 text
- 默认禁用缓存（注入唯一 nonce），降低缓存命中对结果的影响
- 支持自定义 base_url（代理或网关）
- 支持配置文件加载与 JSON 结果落盘
- 支持将每轮正文输出写入文件，便于抽样检查

## 快速开始

1. 安装依赖

```bash
pip install anthropic
```

1. 配置密钥（推荐环境变量）

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

1. 直接运行

```bash
python ttft_benchmark.py
```

## 常用命令

```bash
# 1) 指定模型、轮数与 prompt
python ttft_benchmark.py --runs 10 --model claude-opus-4.8 --prompt "你好"

# 2) 启用 adaptive thinking
python ttft_benchmark.py --thinking --effort high

# 3) 预热 1 轮、正式 20 轮，并输出 JSON
python ttft_benchmark.py --warmup 1 --runs 20 --json result.json

# 4) 从配置文件读取参数
python ttft_benchmark.py --config ttft_config.json

# 5) 允许缓存（默认 no-cache）
python ttft_benchmark.py --allow-cache

# 6) 写出每轮正文到 out/ 目录
python ttft_benchmark.py --output-dir out

# 7) 仅按正文首个 text_delta 统计 TTFT
python ttft_benchmark.py --ttft-mode text
```

## 参数说明

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| --config | JSON 配置文件路径 | - |
| --model | 模型 ID | claude-opus-4-6 |
| --prompt | 测试 prompt | 用一句话介绍你自己。 |
| --runs | 正式测量轮数 | 5 |
| --warmup | 预热轮数（不计入统计） | 1 |
| --max-tokens | max_tokens（测 TTFT 通常不需要很大） | 128 |
| --thinking | 启用 adaptive thinking | false |
| --effort | 推理努力程度（需配合 --thinking） | - |
| --ttft-mode | TTFT 触发口径：any 或 text | any |
| --base-url | 自定义 API base_url | - |
| --json | 将结果写入 JSON 文件 | - |
| --no-cache / --allow-cache | 是否注入 nonce 绕过 prompt 缓存 | --no-cache |
| --api-key | API 密钥 | - |
| --output-dir | 每轮正文输出目录 | - |

参数优先级：命令行参数 > 配置文件（--config）> 内置默认值

API 密钥优先级：--api-key > ANTHROPIC_API_KEY > 配置文件 api_key

## 配置文件建议

仓库中的 ttft_config.json 建议作为可提交模板使用，api_key 保持空字符串。

本地可使用 ttft_config.json.local 保存个人配置与敏感信息（该文件通常不提交）。

支持的配置键：

- model
- prompt
- runs
- warmup
- max_tokens
- thinking
- base_url
- json_out（也支持别名 json）
- no_cache
- api_key
- effort
- output_dir
- ttft_mode

ttft_mode 取值：

- any：首个 text_delta 或 thinking_delta 即触发 TTFT
- text：仅首个 text_delta 触发 TTFT（更接近用户看到正文的等待时间）

## 输出解读

控制台会输出每轮：

- TTFT
- total（总耗时）
- out_tokens（输出 token 数）

并在最后汇总：

- TTFT 统计指标
- 成功率（success/total）

如果配置了 --json（或 json_out），会生成结果文件（例如 ttft_result.json），包含：

- 合并后的运行配置
- 每轮明细
- TTFT 和 total 的统计摘要
- 成功率

如果配置了 --output-dir（或 output_dir），每轮正文会写入独立文件：

- 预热轮：warmup.txt
- 正式轮：run_001.txt、run_002.txt...

说明：当前实现仅写出正文 text，思考内容默认不落盘。
