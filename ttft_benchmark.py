#!/usr/bin/env python3
"""测量访问 Claude 模型的首个 Token 响应时间 (TTFT, Time To First Token)。

TTFT = 从发出请求到收到第一个有效内容增量 (text / thinking) 的耗时。
脚本使用流式接口逐事件读取，记录第一个 content delta 到达的时刻。

用法:
    export ANTHROPIC_API_KEY=sk-ant-...
    python ttft_benchmark.py
    python ttft_benchmark.py --runs 10 --model claude-opus-4-6 --prompt "你好"
    python ttft_benchmark.py --thinking          # 开启 adaptive thinking
    python ttft_benchmark.py --warmup 1 --runs 20 --json result.json
    python ttft_benchmark.py --config ttft_config.json   # 从配置文件读取参数
    python ttft_benchmark.py --allow-cache               # 允许缓存（默认是 --no-cache）
    python ttft_benchmark.py --output-dir out            # 将模型输出正文写入 out/ 目录
    python ttft_benchmark.py --ttft-mode text            # 仅以正文 text_delta 计算 TTFT

参数优先级: 命令行参数 > 配置文件(--config) > 内置默认值
API 密钥优先级: --api-key > 环境变量 ANTHROPIC_API_KEY > 配置文件 api_key

依赖:
    pip install anthropic
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, asdict


@dataclass
class RunResult:
    index: int
    ttft_s: float | None          # 首 token 时间（秒）
    total_s: float | None         # 整体完成时间（秒）
    output_tokens: int | None
    ok: bool
    error: str | None = None


def measure_once(client, index: int, model: str, prompt: str,
                 max_tokens: int, use_thinking: bool,
                 no_cache: bool = True, effort: str | None = None,
                 output_dir: str | None = None,
                 ttft_mode: str = "any") -> RunResult:
    """执行一次流式请求并测量 TTFT。

    no_cache=True 时为每次请求注入唯一 nonce，使 prompt 内容唯一，
    从而绕过服务端 prompt 缓存（prompt cache），保证 TTFT 测量不被缓存命中干扰。
    脚本始终不附加 cache_control 块，因此不会主动写入缓存。
    effort 设置推理努力程度（如 low/medium/high），注入 thinking 配置。
    output_dir 不为空时，将本次生成的正文（text）写入该目录下的独立文件。
    ttft_mode="any" 时首个 text_delta/thinking_delta 均算 TTFT；
    ttft_mode="text" 时仅 text_delta 算 TTFT。
    """
    content = prompt
    if no_cache:
        # 唯一 nonce 使每次请求的输入前缀都不同，避免命中任何缓存
        content = f"[nonce:{uuid.uuid4().hex} ts:{time.time_ns()}]\n{prompt}"

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
        "stream": True,
    }
    if use_thinking:
        thinking: dict = {"type": "adaptive"}
        if effort:
            thinking["effort"] = effort
        kwargs["thinking"] = thinking

    start = time.perf_counter()
    ttft: float | None = None
    output_tokens: int | None = None
    text_parts: list[str] = []  # 累积模型生成的正文增量
    ttft_delta_types = ("text_delta",) if ttft_mode == "text" else ("text_delta", "thinking_delta")

    try:
        # 使用底层 messages.create(stream=True) 原始事件迭代，
        # 避免 SDK .stream() 辅助器的快照累积逻辑（部分网关返回 text=None 会触发其崩溃）。
        stream = client.messages.create(**kwargs)
        for event in stream:
            etype = getattr(event, "type", None)
            if etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)
                if delta_type in ttft_delta_types and ttft is None:
                    ttft = time.perf_counter() - start
                # 仅累积正文（text）；thinking 在 Opus 4.8 上默认不返回内容
                if delta_type == "text_delta":
                    txt = getattr(delta, "text", None)
                    if txt:
                        text_parts.append(txt)
            elif etype == "message_delta":
                usage = getattr(event, "usage", None)
                if usage and getattr(usage, "output_tokens", None) is not None:
                    output_tokens = usage.output_tokens
            elif etype == "message_start":
                msg = getattr(event, "message", None)
                if msg and getattr(msg, "usage", None) and \
                        getattr(msg.usage, "output_tokens", None) is not None:
                    output_tokens = msg.usage.output_tokens
        total = time.perf_counter() - start
        if output_dir:
            _write_output(output_dir, index, "".join(text_parts))
        return RunResult(index, ttft, total, output_tokens, ok=True)
    except Exception as exc:  # noqa: BLE001 - 基准测试需捕获所有错误并记录
        total = time.perf_counter() - start
        if output_dir and text_parts:
            _write_output(output_dir, index, "".join(text_parts))
        return RunResult(index, None, total, None, ok=False, error=f"{type(exc).__name__}: {exc}")


def _write_output(output_dir: str, index: int, text: str) -> None:
    """将单次运行的模型正文写入 output_dir 下的独立文件。

    index == -1 表示预热轮，文件名标记为 warmup。
    """
    os.makedirs(output_dir, exist_ok=True)
    name = "warmup.txt" if index < 0 else f"run_{index + 1:03d}.txt"
    with open(os.path.join(output_dir, name), "w", encoding="utf-8") as f:
        f.write(text)


def summarize(values: list[float]) -> dict | None:
    if not values:
        return None
    s = sorted(values)

    def pct(p: float) -> float:
        if len(s) == 1:
            return s[0]
        rank = p / 100 * (len(s) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (rank - lo)

    return {
        "count": len(s),
        "min_s": round(min(s), 4),
        "max_s": round(max(s), 4),
        "mean_s": round(statistics.fmean(s), 4),
        "median_s": round(statistics.median(s), 4),
        "p90_s": round(pct(90), 4),
        "p95_s": round(pct(95), 4),
        "p99_s": round(pct(99), 4),
        "stdev_s": round(statistics.pstdev(s), 4) if len(s) > 1 else 0.0,
    }


# 内置默认值。优先级: 命令行参数 > 配置文件 > 内置默认值。
DEFAULTS: dict = {
    "model": "claude-opus-4-6",
    "prompt": "用一句话介绍你自己。",
    "runs": 5,
    "warmup": 1,
    "max_tokens": 128,
    "thinking": False,
    "base_url": None,
    "json_out": None,
    "no_cache": True,
    "api_key": None,
    "effort": None,
    "output_dir": None,
    "ttft_mode": "any",
}


def load_config(path: str) -> dict:
    """从 JSON 配置文件加载参数，仅保留已知键。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象 (dict)。")
    # 兼容 "json" 作为 "json_out" 的别名
    if "json" in raw and "json_out" not in raw:
        raw["json_out"] = raw.pop("json")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        print(f"警告: 配置文件包含未知键，将被忽略: {sorted(unknown)}", file=sys.stderr)
    return {k: v for k, v in raw.items() if k in DEFAULTS}


def main() -> int:
    parser = argparse.ArgumentParser(description="测量 Claude 模型首 Token 响应时间 (TTFT)")
    # default=None 用于区分“用户未指定”与“显式赋值”，便于与配置文件合并
    parser.add_argument("--config", default=None, help="JSON 参数配置文件路径")
    parser.add_argument("--model", default=None, help="模型 ID")
    parser.add_argument("--prompt", default=None, help="测试用 prompt")
    parser.add_argument("--runs", type=int, default=None, help="正式测量轮数")
    parser.add_argument("--warmup", type=int, default=None, help="预热轮数（不计入统计）")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None,
                        help="max_tokens（小值即可，TTFT 主要看首 token）")
    parser.add_argument("--thinking", action="store_true", default=None, help="启用 adaptive thinking")
    parser.add_argument("--base-url", dest="base_url", default=None, help="自定义 API base_url（代理/网关时使用）")
    parser.add_argument("--json", dest="json_out", default=None, help="将结果写入 JSON 文件")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--no-cache", dest="no_cache", action="store_true", default=None,
                             help="禁用缓存：为每次请求注入唯一 nonce 绕过 prompt 缓存（默认开启）")
    cache_group.add_argument("--allow-cache", dest="no_cache", action="store_false", default=None,
                             help="允许缓存：不注入 nonce，使用原始 prompt")
    parser.add_argument("--api-key", dest="api_key", default=None,
                        help="API 密钥（优先级最高；也可用环境变量 ANTHROPIC_API_KEY 或配置文件）")
    parser.add_argument("--effort", dest="effort", default=None,
                        choices=["low", "medium", "high"],
                        help="推理努力程度（需配合 --thinking 使用）")
    parser.add_argument("--output-dir", dest="output_dir", default=None,
                        help="将每轮模型生成的正文写入该目录（每轮一个文件）")
    parser.add_argument("--ttft-mode", dest="ttft_mode", default=None,
                        choices=["any", "text"],
                        help="TTFT 触发口径：any=text_delta/thinking_delta，text=仅 text_delta")
    args = parser.parse_args()

    # 合并配置: 内置默认值 -> 配置文件 -> 命令行
    cfg = dict(DEFAULTS)
    if args.config:
        try:
            cfg.update(load_config(args.config))
        except FileNotFoundError:
            print(f"配置文件不存在: {args.config}", file=sys.stderr)
            return 2
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"配置文件解析失败: {exc}", file=sys.stderr)
            return 2
    for key in DEFAULTS:
        cli_val = getattr(args, key, None)
        if cli_val is not None:
            cfg[key] = cli_val

    if cfg["ttft_mode"] not in ("any", "text"):
        print("ttft_mode 只能是 'any' 或 'text'。", file=sys.stderr)
        return 2

    try:
        from anthropic import Anthropic
    except ImportError:
        print("缺少依赖，请先安装: pip install anthropic", file=sys.stderr)
        return 2

    # 密钥优先级: 命令行 --api-key > 环境变量 ANTHROPIC_API_KEY > 配置文件 api_key
    api_key = cfg["api_key"] or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("未提供 API 密钥：请设置环境变量 ANTHROPIC_API_KEY、传入 --api-key 或在配置文件中设置 api_key。",
              file=sys.stderr)
        return 2

    client_kwargs: dict = {"api_key": api_key}
    if cfg["base_url"]:
        client_kwargs["base_url"] = cfg["base_url"]
    client = Anthropic(**client_kwargs)

    print(f"模型: {cfg['model']} | runs={cfg['runs']} | warmup={cfg['warmup']} | "
          f"max_tokens={cfg['max_tokens']} | thinking={cfg['thinking']} | "
            f"effort={cfg['effort']} | no_cache={cfg['no_cache']} | "
            f"ttft_mode={cfg['ttft_mode']}")
    print(f"prompt: {cfg['prompt']!r}\n")

    # 预热
    for w in range(cfg["warmup"]):
        r = measure_once(client, -1, cfg["model"], cfg["prompt"], cfg["max_tokens"],
                         cfg["thinking"], cfg["no_cache"], cfg["effort"],
                         cfg["output_dir"], cfg["ttft_mode"])
        status = "ok" if r.ok else f"FAIL ({r.error})"
        ttft = f"{r.ttft_s:.4f}s" if r.ttft_s is not None else "—"
        print(f"  [warmup {w + 1}/{cfg['warmup']}] TTFT={ttft} {status}")

    results: list[RunResult] = []
    for i in range(cfg["runs"]):
        r = measure_once(client, i, cfg["model"], cfg["prompt"], cfg["max_tokens"],
                         cfg["thinking"], cfg["no_cache"], cfg["effort"],
                         cfg["output_dir"], cfg["ttft_mode"])
        results.append(r)
        if r.ok:
            print(f"  [run {i + 1}/{cfg['runs']}] TTFT={r.ttft_s:.4f}s  "
                  f"total={r.total_s:.4f}s  out_tokens={r.output_tokens}")
        else:
            print(f"  [run {i + 1}/{cfg['runs']}] FAILED: {r.error}")

    ttfts = [r.ttft_s for r in results if r.ok and r.ttft_s is not None]
    totals = [r.total_s for r in results if r.ok and r.total_s is not None]
    summary_ttft = summarize(ttfts)
    summary_total = summarize(totals)
    success = sum(1 for r in results if r.ok)

    print("\n===== 统计结果 (TTFT) =====")
    if summary_ttft:
        for k, v in summary_ttft.items():
            print(f"  {k:>10}: {v}")
    else:
        print("  无成功样本，无法统计 TTFT。")
    print(f"\n成功率: {success}/{cfg['runs']}")
    if cfg["output_dir"]:
        print(f"模型输出正文已写入目录: {cfg['output_dir']}")

    if cfg["json_out"]:
        payload = {
            "config": {
                "model": cfg["model"],
                "prompt": cfg["prompt"],
                "runs": cfg["runs"],
                "warmup": cfg["warmup"],
                "max_tokens": cfg["max_tokens"],
                "thinking": cfg["thinking"],
                "base_url": cfg["base_url"],
                "no_cache": cfg["no_cache"],
                "effort": cfg["effort"],
                "output_dir": cfg["output_dir"],
                "ttft_mode": cfg["ttft_mode"],
            },
            "runs": [asdict(r) for r in results],
            "summary_ttft_s": summary_ttft,
            "summary_total_s": summary_total,
            "success_rate": f"{success}/{cfg['runs']}",
        }
        with open(cfg["json_out"], "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n已写入: {cfg['json_out']}")

    return 0 if success == cfg["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
