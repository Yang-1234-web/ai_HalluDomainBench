# verify.py
import asyncio
import json
import re
import socket
import aiohttp
from pathlib import Path
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# --- 配置区 ---
ROOT = Path(__file__).resolve().parents[0]
INPUT_FILE = Path(r"C:\Users\LENOVO\PycharmProjects\PythonProjectAI\data\response\model_real_outputs.jsonl")
OUTPUT_FILE = Path(r"C:\Users\LENOVO\PycharmProjects\PythonProjectAI\data\response\verified_links.jsonl")
CONCURRENCY_LIMIT = 50  # 异步并发控制
PROXY_URL = "http://127.0.0.1:7897"

# 白名单正则表达式
URL_PATTERN = re.compile(r'(?:https?://|www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[a-zA-Z0-9./\-_?=&#%]*')


class DeterministicError(Exception):
    """确定性错误（如404, 找不到域名），无需重试"""
    pass


class TemporaryError(Exception):
    """临时性错误（如超时, 502），建议重试"""
    pass


class LinkVerifier:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    def extract_urls(self, text: str) -> list[str]:
        """从文本中提取 URL 并进行初步去重"""
        urls = URL_PATTERN.findall(text)
        clean_urls = []
        for u in urls:
            u = u.rstrip('.,;:')
            if u.startswith("www."):
                u = "http://" + u
            clean_urls.append(u)
        return list(set(clean_urls))

    async def verify_dns(self, domain: str) -> bool:
        """DNS 预检"""
        if not domain or len(domain) > 255:
            return False

        clean_domain = domain.split(':')[0].strip("./\\:\"'()[]{} ")
        if not clean_domain or not re.match(r'^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$', clean_domain):
            return False

        loop = asyncio.get_running_loop()
        try:
            # 即使在国内，被墙的域名通常也会被 DNS 污染返回假 IP，而不是解析失败。
            # 所以这里的原生 DNS 验证依然能有效剔除“格式正确但现实中根本不存在”的纯瞎编域名。
            await loop.getaddrinfo(clean_domain, None)
            return True
        except socket.gaierror:
            return False
        except Exception:
            return False

    @retry(
        retry=retry_if_exception_type(TemporaryError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def check_http_status(self, session: aiohttp.ClientSession, url: str) -> dict:
        """协议兜底与错误细分（加入代理支持）"""
        try:
            # 【核心修改：传入 proxy=PROXY_URL】
            # aiohttp 会将这个请求通过你的梯子发送出去，完美绕过 GFW
            async with session.head(url, timeout=10, allow_redirects=True, proxy=PROXY_URL) as resp:
                if resp.status < 400:
                    return {"status": "live", "code": resp.status}
                elif resp.status in [404, 410]:
                    raise DeterministicError(f"HTTP {resp.status}")
                elif resp.status >= 500:
                    raise TemporaryError(f"HTTP {resp.status}")
                else:
                    return {"status": "dead", "code": resp.status}
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            raise TemporaryError(str(e))

    async def verify_single_url(self, session: aiohttp.ClientSession, url: str) -> dict:
        """单条链接验证流水线"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split('/')[0]
            if not domain:
                raise ValueError("Empty domain")
        except ValueError:
            return {"url": url, "result": "dead", "reason": "Malformed URL Format"}

        # 1. DNS 预检
        dns_ok = await self.verify_dns(domain)
        if not dns_ok:
            return {"url": url, "result": "dead", "reason": "NXDOMAIN"}

        # 2. HTTP/HTTPS 验证
        try:
            async with self.semaphore:
                res = await self.check_http_status(session, url)
                return {"url": url, "result": res["status"], "reason": f"Code {res['code']}"}
        except DeterministicError as e:
            return {"url": url, "result": "dead", "reason": str(e)}
        except Exception as e:
            return {"url": url, "result": "dead", "reason": f"Connection Failed"}

    async def process_row(self, session: aiohttp.ClientSession, row: dict) -> dict:
        """处理 JSONL 中的一行数据"""
        text = row.get("response", "")
        reasoning = row.get("reasoning_content", "")
        combined_text = f"{reasoning}\n{text}"

        urls = self.extract_urls(combined_text)
        results = []
        for url in urls:
            res = await self.verify_single_url(session, url)
            results.append(res)

        row["verified_links"] = results
        return row


async def main():
    verifier = LinkVerifier()

    if not INPUT_FILE.exists():
        print(f"输入文件不存在: {INPUT_FILE}")
        return

    # 开启 aiohttp session
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as session:
        with open(INPUT_FILE, "r", encoding="utf-8") as f, \
                open(OUTPUT_FILE, "w", encoding="utf-8") as out:

            tasks = []
            for line in f:
                if not line.strip(): continue
                row = json.loads(line)
                tasks.append(verifier.process_row(session, row))

            # 并发执行并显示进度
            for i, task in enumerate(asyncio.as_completed(tasks)):
                result_row = await task
                out.write(json.dumps(result_row, ensure_ascii=False) + "\n")
                out.flush()
                print(f"已处理 {i + 1}/{len(tasks)} 条 AI 响应记录")


if __name__ == "__main__":
    import sys

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())