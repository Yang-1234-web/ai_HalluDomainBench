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

# 【核心优化 1：白名单正则表达式】
# 强制规定：必须以 http/https/www 开头，后面只能跟字母、数字、点和合法的 URL 路径符号。
# 遇到星号(*)、中括号([])、小括号()等 Markdown 格式符号会自动被截断丢弃。
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
            # 再次清理末尾可能因为句尾残留的标点符号
            u = u.rstrip('.,;:')
            if u.startswith("www."):
                u = "http://" + u
            clean_urls.append(u)
        return list(set(clean_urls))

    async def verify_dns(self, domain: str) -> bool:
        """【核心优化 2：原生系统级 DNS 解析，100% 准确】"""
        if not domain or len(domain) > 255:
            return False

        clean_domain = domain.split(':')[0].strip("./\\:\"'()[]{} ")
        if not clean_domain or not re.match(r'^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$', clean_domain):
            return False

        loop = asyncio.get_running_loop()
        try:
            # 放弃 aiodns，直接调用底层操作系统的 getaddrinfo，彻底解决 Windows 下的误判
            await loop.getaddrinfo(clean_domain, None)
            return True
        except socket.gaierror:
            # socket.gaierror 意味着在真实的互联网中找不到这个域名
            return False
        except Exception:
            return False

    @retry(
        retry=retry_if_exception_type(TemporaryError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def check_http_status(self, session: aiohttp.ClientSession, url: str) -> dict:
        """协议兜底与错误细分"""
        try:
            async with session.head(url, timeout=10, allow_redirects=True) as resp:
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
        except Exception:
            return {"url": url, "result": "dead", "reason": "Connection Failed"}

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
    # 在 Windows 系统上解决可能出现的 Event Loop 关闭异常
    import sys

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())