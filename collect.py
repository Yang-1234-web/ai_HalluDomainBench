from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

# 假设 api_client 模块提供 LLMFactory 和 BaseLLMClient
from api_client import LLMFactory, BaseLLMClient

ROOT = Path(__file__).resolve().parents[0]

# ==========================================
# 需要测试的模型列表（选择代表性模型）
# ==========================================
TARGET_MODELS = [
    "Pro/zai-org/GLM-5",            # GLM系列最新模型
    "moonshotai/Kimi-K2-Thinking",   # Kimi系列思考模型
    "Qwen/Qwen3.5-397B-A17B",        # Qwen系列大模型
    "deepseek-ai/DeepSeek-V3.2",     # DeepSeek系列最新模型
    "baidu/ERNIE-4.5-300B-A47B",     # 百度ERNIE系列大模型
    "internlm/internlm2_5-7b-chat",   # 智谱AI模型
    
    # 以下模型暂时注释掉以减少API调用成本
    # "Qwen/Qwen3.5-35B-A3B",
    # "Qwen/Qwen3.5-27B",
    # "Qwen/Qwen3.5-9B",
    # "Qwen/Qwen3.5-4B",
    # "Qwen/Qwen3-VL-32B-Instruct",
    # "Qwen/Qwen3-VL-32B-Thinking",
    # "Qwen/Qwen3-VL-8B-Instruct",
    # "Qwen/Qwen3-VL-8B-Thinking",
    # "Qwen/Qwen3.5-122B-A10B",
    # "Pro/deepseek-ai/DeepSeek-V3.2",
    # "deepseek-ai/DeepSeek-V3.1-Terminus",
    # "Pro/deepseek-ai/DeepSeek-V3.1-Terminus",
    # "deepseek-ai/DeepSeek-R1",
    # "Pro/deepseek-ai/DeepSeek-R1",
    # "deepseek-ai/DeepSeek-V3",
    # "Pro/deepseek-ai/DeepSeek-V3",
    # "deepseek-ai/DeepSeek-OCR",
    # "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
    # "Pro/zai-org/GLM-4.7",
    # "zai-org/GLM-4.6V",
    # "zai-org/GLM-4.6",
    # "zai-org/GLM-4.5V",
    # "zai-org/GLM-4.5-Air",
    # "Pro/moonshotai/Kimi-K2.5",
    # "Pro/moonshotai/Kimi-K2-Thinking",
    # "moonshotai/Kimi-K2-Instruct-0905",
    # "inclusionAI/Ring-flash-2.0",
    # "inclusionAI/Ling-flash-2.0",
    # "stepfun-ai/Step-3.5-Flash",
    # "Pro/MiniMaxAI/MiniMax-M2.5",
    # "tencent/Hunyuan-MT-7B",
    # "tencent/Hunyuan-A13B-Instruct",
    # "PaddlePaddle/PaddleOCR-VL-1.5",
    # "PaddlePaddle/PaddleOCR-VL",
    # "baidu/ERNIE-4.5-300B-A47B",
]

def load_prompts_from_csv(path: Path) -> list[dict]:
    """从 CSV 文件加载 prompts，返回字典列表。"""
    prompts = []
    required_fields = ["prompt_id", "question", "life_domain", "scenario",
                       "risk_tier", "prompt_style", "ambiguity_level",
                       "context_noise", "urgency"]
    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                p = {field: row.get(field, "") for field in required_fields}
                if not p["prompt_id"] or not p["question"]:
                    continue
                prompts.append(p)
    except FileNotFoundError:
        print(f"错误: 找不到 prompts 文件 {path}")
        raise SystemExit(1)
    except Exception as e:
        print(f"读取 prompts 文件失败: {e}")
        raise SystemExit(1)
    return prompts


def load_existing_pairs(path: Path) -> set[tuple[str, str]]:
    """加载输出文件中已有的 (model, prompt_id) 对，用于断点续传。"""
    if not path.exists():
        return set()
    seen = set()
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


def process_one(
        prompt: dict,
        model: str,
        client_cache: dict,
        api_keys: dict,
        args: argparse.Namespace,
        lock: Lock,
        total_jobs: int,
        done_counter: list,
        written_counter: list,
        skipped_counter: list,
        seen: set,
        out_file: Path
) -> None:
    """处理单个 (prompt, model) 任务，线程安全的写入和计数。"""
    prompt_id = prompt["prompt_id"]
    question = prompt["question"]

    # 检查是否已存在（断点续传）
    pair = (model, prompt_id)
    with lock:
        if pair in seen:
            skipped_counter[0] += 1
            print(f"[{done_counter[0] + 1}/{total_jobs}] skip {model} {prompt_id}")
            done_counter[0] += 1
            return
        # 立即将当前 job 加入 seen，避免并发重复处理
        seen.add(pair)

    # 获取或创建客户端
    with lock:
        if model not in client_cache:
            try:
                client_cache[model] = LLMFactory.get_client(model, api_keys)
            except ValueError as e:
                print(f"[{done_counter[0] + 1}/{total_jobs}] 路由错误: {model} - {e}")
                done_counter[0] += 1
                return
        client = client_cache[model]

    # 重试逻辑
    attempt = 0
    last_err = ""
    response_text = ""
    reasoning_content = ""
    usage = {}
    finish_reason = ""

    while attempt <= args.max_retries:
        attempt += 1
        try:
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
            backoff = min(0.8 * (2 ** (attempt - 1)) + random.random() * 0.35, 10.0)
            with lock:
                print(
                    f"[{done_counter[0] + 1}/{total_jobs}] retry {attempt}/{args.max_retries} "
                    f"{model} {prompt_id} ({last_err[:120]})"
                )
            time.sleep(backoff)

    # 构建输出行
    row = {
        "model": model,
        "prompt_id": prompt_id,
        "response": response_text,
        "reasoning_content": reasoning_content,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "meta": {
            "life_domain": prompt.get("life_domain", ""),
            "scenario": prompt.get("scenario", ""),
            "risk_tier": prompt.get("risk_tier", ""),
            "prompt_style": prompt.get("prompt_style", ""),
            "ambiguity_level": prompt.get("ambiguity_level", ""),
            "context_noise": prompt.get("context_noise", ""),
            "urgency": prompt.get("urgency", ""),
            "finish_reason": finish_reason,
            "usage": usage,
            "error": last_err if not response_text else "",
        },
    }

    # 线程安全写入文件
    with lock:
        with out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
        written_counter[0] += 1
        done_counter[0] += 1
        reasoning_hint = " (+思维链)" if reasoning_content else ""
        print(f"[{done_counter[0]}/{total_jobs}] ok {model} {prompt_id} len={len(response_text)}{reasoning_hint}")

    if args.sleep_sec > 0:
        time.sleep(args.sleep_sec)


def main():
    parser = argparse.ArgumentParser(description="并发收集模型响应")
    parser.add_argument(
        "--prompts",
        type=Path,
        default=ROOT / "data" / "prompts" / "prompts_rich.csv",
        help="Prompt CSV 文件路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "response" / "model_real_outputs.jsonl",
        help="输出 JSONL 文件路径",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default="",
        help="可选的系统提示，留空表示无",
    )
    parser.add_argument("--max-prompts", type=int, default=0, help="0 表示使用所有 prompts")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout-sec", type=float, default=300.0)
    parser.add_argument("--sleep-sec", type=float, default=2, help="每个请求后的休眠秒数（用于限流）")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="跳过输出文件中已存在的 (model, prompt_id) 对",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="并发工作线程数",
    )
    args = parser.parse_args()

    api_keys = {
        "SILICONFLOW_API_KEY": os.getenv("SILICONFLOW_API_KEY", "sk-ghumlmbkammvsaoorjbygckpaaivhpcapcwkqwxjupvghtsd")}
    if not api_keys["SILICONFLOW_API_KEY"]:
        print("警告: 未设置环境变量 SILICONFLOW_API_KEY，请求可能会失败。")

    # =========================================================================
    # 【修改部分开始】：从 new_dataset.json 读取测试数据
    # =========================================================================
    # all_prompts = load_prompts_from_csv(args.prompts)

    import json
    with open('new_dataset.json', 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    all_prompts = []
    for i, item in enumerate(dataset, 1):
        prompt = {
            "prompt_id": f"TEST_{i:03d}",
            "question": item["prompt"],
            "life_domain": item["domain"],
            "scenario": item["domain"],
            "risk_tier": "medium",
            "prompt_style": "direct",
            "ambiguity_level": "low",
            "context_noise": "low",
            "urgency": "low"
        }
        all_prompts.append(prompt)
    # =========================================================================
    # 【修改部分结束】
    # =========================================================================

    if args.max_prompts > 0:
        all_prompts = all_prompts[: args.max_prompts]
    if not all_prompts:
        print("错误: 没有可用的 prompts")
        raise SystemExit(1)

    models = TARGET_MODELS
    if not models:
        print("错误: TARGET_MODELS 列表为空")
        raise SystemExit(1)

    # 创建输出目录
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # 加载已存在的记录（用于 --resume）
    seen = load_existing_pairs(args.output) if args.resume else set()

    total_jobs = len(all_prompts) * len(models)
    done_counter = [0]
    written_counter = [0]
    skipped_counter = [0]

    client_cache = {}
    write_lock = Lock()

    print(f"开始处理 {total_jobs} 个任务，并发数 {args.workers}...")

    # 使用线程池并发执行
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for p in all_prompts:
            for m in models:
                futures.append(
                    executor.submit(
                        process_one,
                        p,
                        m,
                        client_cache,
                        api_keys,
                        args,
                        write_lock,
                        total_jobs,
                        done_counter,
                        written_counter,
                        skipped_counter,
                        seen,
                        args.output,
                    )
                )

        for future in as_completed(futures):
            future.result()

    print("\n数据收集完成。")
    print(f"总任务数: {total_jobs}")
    print(f"成功写入: {written_counter[0]}")
    print(f"跳过(已存在): {skipped_counter[0]}")
    print(f"输出文件: {args.output}")


if __name__ == "__main__":
    main()