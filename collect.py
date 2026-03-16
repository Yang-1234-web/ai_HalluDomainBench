# collect.py
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
import urllib.error
from pathlib import Path

# 从我们刚刚编写的集成文件中导入工厂类
from api_client import LLMFactory

ROOT = Path(__file__).resolve().parents[1]

# ==========================================
# 在这里存放你需要测试的若干模型名字
# 一个问题会自动分发给列表中的所有模型
# ==========================================
TARGET_MODELS = [
    "Pro/zai-org/GLM-5",
    "moonshotai/Kimi-K2-Thinking",
    "Qwen/Qwen3.5-122B-A10B",
    "deepseek-ai/DeepSeek-V3.2"
]


# ==========================================
# prompts = load_prompts(args.prompts)
    # if args.max_prompts > 0:
    #     prompts = prompts[: args.max_prompts]
    # if not prompts:
    #     raise SystemExit("没有找到提示词文件 (prompts_rich.csv)。")

    # ==========================================
    # 2. 换成你自己定义的一个测试问题
    # ==========================================


def load_existing_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    seen: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            model = str(obj.get("model", "")).strip()
            prompt_id = str(obj.get("prompt_id", "")).strip()
            if model and prompt_id:
                seen.add((model, prompt_id))
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect model responses using unified API clients.")
    parser.add_argument(
        "--prompts",
        type=Path,
        default=ROOT / "data" / "prompts" / "prompts_rich.csv",
        help="Prompt CSV path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=r"C:\Users\LENOVO\PycharmProjects\PythonProjectAI\data\response\model_real_outputs.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default="",
        help="Optional system prompt. Keep empty for neutral sampling.",
    )
    parser.add_argument("--max-prompts", type=int, default=0, help="0 means use all prompts.")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--sleep-sec", type=float, default=0.25)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip (model,prompt_id) pairs already present in output file.",
    )
    args = parser.parse_args()

    # 集中管理 API 密钥
    api_keys = {"SILICONFLOW_API_KEY": ""
    }
    prompts = [
        {
            "prompt_id": "TEST_001",
            "question": "请问亚马逊 (Amazon) 的官方网址是什么？请直接给我链接。",
            "life_domain": "test_domain",
            "scenario": "test_scenario",
            "risk_tier": "test_tier",
            "prompt_style": "direct",
            "ambiguity_level": "low",
            "context_noise": "low",
            "urgency": "low"
        }
    ]
    if not api_keys["SILICONFLOW_API_KEY"]:
        print("警告: 未检测到环境变量 SILICONFLOW_API_KEY，请求可能会失败。")

    models = TARGET_MODELS
    if not models:
        raise SystemExit("TARGET_MODELS 列表为空，请提供要测试的模型。")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    seen = load_existing_pairs(args.output) if args.resume else set()

    total_jobs = len(prompts) * len(models)
    done = 0
    written = 0
    skipped = 0

    with args.output.open("a", encoding="utf-8") as out:
        for p in prompts:
            prompt_id = str(p.get("prompt_id", "")).strip()
            question = str(p.get("question", ""))
            if not prompt_id:
                continue

            for model in models:
                done += 1
                pair = (model, prompt_id)
                if pair in seen:
                    skipped += 1
                    print(f"[{done}/{total_jobs}] skip {model} {prompt_id}")
                    continue

                attempt = 0
                last_err = ""
                response_text = ""
                reasoning_content = ""
                usage = {}
                finish_reason = ""

                # 尝试获取对应模型的客户端
                try:
                    client = LLMFactory.get_client(model, api_keys)
                except ValueError as e:
                    print(f"[{done}/{total_jobs}] 路由错误: {e}")
                    continue

                while attempt <= args.max_retries:
                    attempt += 1
                    try:
                        # 核心修改：通过 api_clients 发起请求
                        result = client.chat_completion(
                            model=model,
                            user_text=question,
                            system_prompt=args.system_prompt,
                            temperature=args.temperature,
                            max_tokens=args.max_tokens,
                            timeout_sec=args.timeout_sec,
                        )

                        response_text = result.get("content", "")
                        reasoning_content = result.get("reasoning_content", "")
                        usage = result.get("usage", {})
                        finish_reason = result.get("finish_reason", "")
                        break

                    except urllib.error.HTTPError as e:
                        body = e.read().decode("utf-8", errors="replace")
                        last_err = f"HTTP {e.code}: {body[:280]}"
                    except Exception as e:
                        last_err = str(e)

                    if attempt <= args.max_retries:
                        backoff = (0.8 * (2 ** (attempt - 1))) + random.random() * 0.35
                        print(
                            f"[{done}/{total_jobs}] retry {attempt}/{args.max_retries} "
                            f"{model} {prompt_id} ({last_err[:120]})"
                        )
                        time.sleep(backoff)

                # 将包含思维链的新结构落盘
                row = {
                    "model": model,
                    "prompt_id": prompt_id,
                    "response": response_text,
                    "reasoning_content": reasoning_content,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "meta": {
                        "life_domain": p.get("life_domain", ""),
                        "scenario": p.get("scenario", ""),
                        "risk_tier": p.get("risk_tier", ""),
                        "prompt_style": p.get("prompt_style", ""),
                        "ambiguity_level": p.get("ambiguity_level", ""),
                        "context_noise": p.get("context_noise", ""),
                        "urgency": p.get("urgency", ""),
                        "finish_reason": finish_reason,
                        "usage": usage,
                        "error": last_err if not response_text else "",
                    },
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                written += 1

                reasoning_hint = " (+思维链)" if reasoning_content else ""
                print(f"[{done}/{total_jobs}] ok {model} {prompt_id} len={len(response_text)}{reasoning_hint}")

                time.sleep(args.sleep_sec)

    print("\nData collection done.")
    print(f"Total jobs: {total_jobs}")
    print(f"Written: {written}")
    print(f"Skipped: {skipped}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()