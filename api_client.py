# api_clients.py
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    """
    所有 AI 模型的抽象基类，规范了必须实现的接口方法。
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

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
    严格遵守官方白名单开启 enable_thinking。
    """

    # 完全以官方文档为准的白名单
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
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # 【修正点】：只认白名单，绝不盲目猜测
        is_thinking_model = model in self.THINKING_SUPPORTED_MODELS

        if is_thinking_model:
            payload["enable_thinking"] = True

        req = urllib.request.Request(
            url=self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))

        message_data = body.get("choices", [])[0].get("message", {})

        # 无论 API 有没有明确要求开启思考参数，只要响应体里有 reasoning_content 就提取
        reasoning_content = message_data.get("reasoning_content", "").strip()

        return {
            "content": message_data.get("content", "").strip(),
            "reasoning_content": reasoning_content,
            "finish_reason": body.get("choices", [])[0].get("finish_reason", ""),
            "usage": body.get("usage", {}),
            "raw_response": body
        }


class LLMFactory:
    """
    调用入口：根据模型名称自动分发到对应的客户端
    """

    @staticmethod
    def get_client(model_name: str, api_keys: dict[str, str]) -> BaseLLMClient:
        # 保留 "moonshotai/" 前缀，让系统能正常调用 Kimi，但不会向它发送 enable_thinking 参数
        valid_prefixes = ("Qwen/", "Pro/", "deepseek-ai/", "zai-org/", "tencent/", "moonshotai/")
        if model_name.startswith(valid_prefixes):
            return SiliconFlowClient(
                api_key=api_keys.get("SILICONFLOW_API_KEY", "")
            )
        else:
            raise ValueError(f"暂未支持或无法识别的模型: {model_name}")