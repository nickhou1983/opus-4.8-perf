# TTFT Benchmark

测量访问 Claude 模型的首个 Token 响应时间（TTFT, Time To First Token）的基准测试脚本。

**TTFT** = 从发出请求到收到第一个有效内容增量的耗时。脚本通过流式接口逐事件读取，默认记录第一个 `text_delta` 或 `thinking_delta` 到达的时刻，并额外统计整体完成时间与输出 token 数。可通过 `--ttft-mode text` 改为仅按正文 `text_delta` 计算。

## 特性

- 基于流式 API 精确测量首 token 时间
- 支持预热（warmup）+ 多轮正式测量，输出 min/max/mean/median/p90/p95/p99/stdev 统计
- 支持 adaptive thinking 与推理努力程度（`low` / `medium` / `high`）
- 支持选择 TTFT 触发口径：`any`（正文或思考增量）/ `text`（仅正文增量）
- 默认禁用缓存（为每次请求注入唯一 nonce 绕过 prompt 缓存），保证测量不被缓存命中干扰
- 支持自定义 `base_url`（用于代理 / 网关）
- 支持从 JSON 配置文件读取参数，并可将结果写入 JSON 文件
- 支持将每轮模型生成的正文写入独立文件（`--output-dir`），便于检查实际输出

## 安装

```bash
pip install anthropic
```

## 用法

```bash
# 通过环境变量提供密钥
export ANTHROPIC_API_KEY=sk-ant-...

# 使用内置默认参数运行
python ttft_benchmark.py

# 指定模型、轮数与 prompt
python ttft_benchmark.py --runs 10 --model claude-opus-4.8 --prompt "你好"

# 启用 adaptive thinking
python ttft_benchmark.py --thinking

# 预热 1 轮、正式 20 轮，并将结果写入 JSON
python ttft_benchmark.py --warmup 1 --runs 20 --json result.json

# 从配置文件读取参数
python ttft_benchmark.py --config ttft_config.json

# 允许缓存（默认是 --no-cache）
python ttft_benchmark.py --allow-cache

# 将每轮模型生成的正文写入 out/ 目录（每轮一个文件）
python ttft_benchmark.py --output-dir out

# 仅以最终答案正文的首个 text_delta 计算 TTFT
python ttft_benchmark.py --ttft-mode text
```

## 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--config` | JSON 参数配置文件路径 | — |
| `--model` | 模型 ID | `claude-opus-4-6` |
| `--prompt` | 测试用 prompt | `用一句话介绍你自己。` |
| `--runs` | 正式测量轮数 | `5` |
| `--warmup` | 预热轮数（不计入统计） | `1` |
| `--max-tokens` | `max_tokens`（TTFT 主要看首 token，小值即可） | `128` |
| `--thinking` | 启用 adaptive thinking | `false` |
| `--ttft-mode` | TTFT 触发口径：`any` 表示 `text_delta` / `thinking_delta` 均可触发，`text` 表示仅 `text_delta` | `any` |
| `--base-url` | 自定义 API base_url（代理 / 网关时使用） | — |
| `--json` | 将结果写入 JSON 文件 | — |
| `--no-cache` / `--allow-cache` | 是否注入 nonce 绕过 prompt 缓存 | `--no-cache` |
| `--api-key` | API 密钥 | — |
| `--effort` | 推理努力程度（需配合 `--thinking`） | — |
| `--output-dir` | 将每轮模型生成的正文写入该目录（每轮一个文件） | — |

**参数优先级**：命令行参数 > 配置文件（`--config`）> 内置默认值

**API 密钥优先级**：`--api-key` > 环境变量 `ANTHROPIC_API_KEY` > 配置文件 `api_key`

## 配置文件

`ttft_config.json` 是可提交的模板（`api_key` 留空）。请勿将真实密钥写入此文件；改用环境变量、`--api-key`，或本地副本 `ttft_config.json.local`（已被 `.gitignore` 忽略）。

支持的键与命令行参数同名：`model`、`prompt`、`runs`、`warmup`、`max_tokens`、`thinking`、`base_url`、`json_out`（或别名 `json`）、`no_cache`、`api_key`、`effort`、`output_dir`、`ttft_mode`。

`ttft_mode` 支持两个取值：`any` 表示首个 `text_delta` 或 `thinking_delta` 都可触发 TTFT；`text` 表示只统计最终答案正文的首个 `text_delta`，更适合衡量用户看到正文的等待时间。

## 输出

脚本会在控制台打印每轮 TTFT、总耗时与输出 token 数，并汇总统计与成功率。若指定 `--json` 或配置中的 `json_out`，结果将写入对应 JSON 文件（如 `ttft_result.json`），包含完整配置、逐轮结果、TTFT 与总耗时统计及成功率。

若指定 `--output-dir` 或配置中的 `output_dir`，每轮模型生成的正文将写入该目录下的独立文件：正式轮为 `run_001.txt`、`run_002.txt`……，预热轮为 `warmup.txt`。注意仅写入正文（`text`），思考内容在 Opus 4.8 上默认不返回。`out/`、`out_*/` 目录已被 `.gitignore` 忽略。
