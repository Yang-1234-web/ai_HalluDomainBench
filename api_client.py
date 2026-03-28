# api_client.py
from __future__ import annotations

import json
import requests
from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    """
    所有 AI 模型的抽象基类，规范了必须实现的接口方法。
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        # 引入 requests.Session() 建立连接池。
        # 好处：不用每次请求都重新 TCP 握手，极大降低网络开销和被网关拦截的概率。
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    @abstractmethod
    def chat_completion(
            self,
            model: str,
            user_text: str,
            system_prompt: str = "",
            temperature: float = 0.2,
            max_tokens: int = 512,
            timeout_sec: float = 90.0
    ) -> dict:
        pass


class SiliconFlowClient(BaseLLMClient):
    """
    针对 SiliconFlow (硅基流动) 平台的标准客户端。
    支持流式输出 (Stream) 解析，专治超大模型的假死超时。
    """

    THINKING_SUPPORTED_MODELS = {
        "Pro/zai-org/GLM-5",
        "Pro/zai-org/GLM-4.7",
        "deepseek-ai/DeepSeek-V3.2",
        "Pro/deepseek-ai/DeepSeek-V3.2",
        "zai-org/GLM-4.6",
        "Qwen/Qwen3-8B",
        "Qwen/Qwen3-14B",
        "Qwen/Qwen3-32B",
        "Qwen/Qwen3-30B-A3B",
        "tencent/Hunyuan-A13B-Instruct",
        "zai-org/GLM-4.5V",
        "deepseek-ai/DeepSeek-V3.1-Terminus",
        "Pro/deepseek-ai/DeepSeek-V3.1-Terminus",
        "Qwen/Qwen3.5-397B-A17B",
        "Qwen/Qwen3.5-122B-A10B",
        "Qwen/Qwen3.5-35B-A3B",
        "Qwen/Qwen3.5-27B",
        "Qwen/Qwen3.5-9B",
        "Qwen/Qwen3.5-4B",
    }

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self.base_url = "https://api.siliconflow.cn/v1/chat/completions"

    def chat_completion(
            self,
            model: str,
            user_text: str,
            system_prompt: str = "",
            temperature: float = 0.2,
            max_tokens: int = 512,
            timeout_sec: float = 90.0
    ) -> dict:
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,  # <--- 核心修改：强制开启流式输出
        }

        if model in self.THINKING_SUPPORTED_MODELS:
            payload["enable_thinking"] = True

        content_chunks = []
        reasoning_chunks = []
        finish_reason = ""
        usage = {}

        try:
            # timeout=(连接超时, 读取超时)。只要服务器不断吐出哪怕一个字符，连接就不会断。
            with self.session.post(
                    url=self.base_url,
                    json=payload,
                    stream=True,
                    timeout=(10.0, timeout_sec)
            ) as resp:

                resp.raise_for_status()  # 遇到 4xx/5xx 错误直接抛出异常，交给 collect.py 捕获重试

                # 解析 SSE (Server-Sent Events) 数据流
                for line in resp.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]

                            # 结束标志
                            if data_str.strip() == "[DONE]":
                                break

                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})

                                    # 累加普通回复
                                    if "content" in delta and delta["content"]:
                                        content_chunks.append(delta["content"])

                                    # 累加思维链回复
                                    if "reasoning_content" in delta and delta["reasoning_content"]:
                                        reasoning_chunks.append(delta["reasoning_content"])

                                    # 捕获结束原因
                                    if choices[0].get("finish_reason"):
                                        finish_reason = choices[0]["finish_reason"]

                                # 捕获 Token 使用量 (通常在最后几个 chunk 中出现)
                                if "usage" in chunk and chunk["usage"]:
                                    usage = chunk["usage"]

                            except json.JSONDecodeError:
                                continue

        except requests.exceptions.RequestException as e:
            # 将 requests 的异常转译，兼容你 collect.py 中的捕获逻辑
            raise RuntimeError(f"请求异常: {str(e)}")

        return {
            "content": "".join(content_chunks).strip(),
            "reasoning_content": "".join(reasoning_chunks).strip(),
            "finish_reason": finish_reason,
            "usage": usage,
            "raw_response": "streamed"  # 流式输出不再保留单次完整的原始 body
        }


class LLMFactory:
    @staticmethod
    def get_client(model_name: str, api_keys: dict[str, str]) -> BaseLLMClient:
        valid_prefixes = (
            "Qwen/", "Pro/", "deepseek-ai/", "zai-org/", "tencent/", "moonshotai/",
            "PaddlePaddle/", "baidu/", "internlm/", "inclusionAI/", "stepfun-ai/"
        )
        if model_name.startswith(valid_prefixes):
            return SiliconFlowClient(api_key=api_keys.get("SILICONFLOW_API_KEY", ""))
        else:
            raise ValueError(f"暂未支持或无法识别的模型: {model_name}")