# verify.py
import asyncio
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# --- config ---
ROOT = Path(__file__).resolve().parents[0]
INPUT_FILE = ROOT / "data" / "response" / "model_real_outputs.jsonl"
OUTPUT_FILE = ROOT / "data" / "response" / "verified_links.jsonl"
CONCURRENCY_LIMIT = 100
PROXY_URL = "http://127.0.0.1:7890"

# URL pattern: allow https://, http://, www., and bare domains; tolerant to fullwidth colon/slash
URL_PATTERN = re.compile(
    r'(?:https?[:：]\s*/\s*/|www\.|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})[^\s)，。；、<>"\']*',
    re.I,
)


class DeterministicError(Exception):
    """Non-retryable error (e.g., 404)."""


class TemporaryError(Exception):
    """Retryable error (e.g., 5xx or timeouts)."""


class LinkVerifier:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    def extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text and de-duplicate."""
        # normalize fullwidth punctuation and remove spaces inside protocol
        norm = (
            text.replace("：", ":").replace("／", "/")
            .replace("。", ".").replace("，", ",")
        )
        norm = re.sub(
            r'https?\s*[:：]\s*/\s*/',
            lambda m: m.group(0).replace(" ", "").replace("：", ":").replace("／", "/"),
            norm,
        )
        urls = URL_PATTERN.findall(norm)
        canonical = {}
        for u in urls:
            # strip markdown/quotes/brackets/punctuation
            if "](" in u:
                u = u.split("](")[-1]
            u = u.strip("`'\"<>[]()")
            u = u.strip('*')
            u = u.rstrip('.,;:，。；)）')
            # stop at first delimiter/space to avoid带中文尾注
            u = re.split(r'[，,。；;]\s*', u, 1)[0]
            u = re.split(r'[)）]', u, 1)[0]
            # 如果有冒号且后面不是端口/路径，截断
            u = re.split(r':(?![0-9/])', u, 1)[0]
            # 遇到星号等分隔说明的符号也截断
            u = re.split(r'\*', u, 1)[0]
            u = u.split()[0] if ' ' in u else u
            if u.startswith("www."):
                u = "http://" + u
            if not u.startswith(("http://", "https://")):
                u = "http://" + u
            # 严格截取合法 URL 片段，去掉中文尾注等杂字符
            m_strict = re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~:/?#\\[\\]@!$&\'()*+,;=%-]*)?', u)
            if m_strict:
                u = m_strict.group(0)
            u = u.rstrip('.,;:，。；)）`*\'"')
            # 统一规范用于去重：小写域名，去掉末尾斜杠/反引号/星号
            try:
                parsed = urlparse(u)
                scheme = parsed.scheme or "http"
                netloc = (parsed.netloc or parsed.path.split('/')[0]).lower().rstrip('.')
                # 基础合法性检查：必须有域名且包含点
                if not netloc or '.' not in netloc:
                    continue
                path = parsed.path if parsed.netloc else ""
                norm_url = f"{scheme}://{netloc}{path}".rstrip('/`*')
                key = f"{netloc}{path}".rstrip('/')
            except Exception:
                norm_url = u.rstrip('/`*')
                key = norm_url
            # 去重：同一域名/路径只保留一条，优先 https
            if key in canonical:
                if canonical[key].startswith("http://") and norm_url.startswith("https://"):
                    canonical[key] = norm_url
                continue
            canonical[key] = norm_url
        return list(canonical.values())

    async def verify_dns(self, domain: str) -> bool:
        """DNS pre-check (non-blocking, used only as hint)."""
        if not domain or len(domain) > 255:
            return True  # 不阻断
        clean_domain = domain.split(":")[0].strip("./\\:\"'()[]{} ")
        if not clean_domain or not re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$", clean_domain):
            return True  # 不阻断
        loop = asyncio.get_running_loop()
        try:
            await loop.getaddrinfo(clean_domain, None)
            return True
        except Exception:
            return True  # DNS 失败也继续走 HTTP，避免误杀

    @retry(
        retry=retry_if_exception_type(TemporaryError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
    )
    async def check_http_status(self, session: aiohttp.ClientSession, url: str, proxy=None) -> dict:
        """GET only, classify with forgiving rules."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            async with session.get(url, timeout=12, allow_redirects=True, proxy=proxy, headers=headers) as resp:
                code = resp.status
            if code < 400 or code in (400, 401, 403, 405, 412, 429, 999):
                return {"status": "live", "code": code}
            if code in (404, 410):
                raise DeterministicError(f"HTTP {code}")
            if code == 502:
                return {"status": "dead", "code": code}
            if code >= 500:
                return {"status": "unknown", "code": code}
            return {"status": "unknown", "code": code}
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            raise TemporaryError(str(e))

    async def verify_single_url(self, session: aiohttp.ClientSession, url: str) -> dict:
        """Verify one URL end-to-end with direct + proxy attempts."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split("/")[0]
            if not domain:
                raise ValueError("Empty domain")
        except ValueError:
            return {"url": url, "result": "dead", "reason": "Malformed URL Format"}

        # DNS 仅作提示，不阻断
        await self.verify_dns(domain)

        # attempt 1: direct
        try:
            async with self.semaphore:
                res = await self.check_http_status(session, url, proxy=None)
                return {"url": url, "result": res["status"], "reason": f"Code {res['code']} (direct)"}
        except DeterministicError as e:
            return {"url": url, "result": "dead", "reason": str(e)}
        except TemporaryError:
            pass
        except Exception:
            pass

        # attempt 2: proxy (if configured)
        if PROXY_URL:
            try:
                async with self.semaphore:
                    res = await self.check_http_status(session, url, proxy=PROXY_URL)
                    return {"url": url, "result": res["status"], "reason": f"Code {res['code']} (proxy)"}
            except DeterministicError as e:
                return {"url": url, "result": "dead", "reason": str(e)}
            except Exception:
                pass

        return {"url": url, "result": "unknown", "reason": "Connection Failed"}

    async def process_row(self, session: aiohttp.ClientSession, row: dict) -> dict:
        """Process one JSONL row: extract URLs and verify."""
        text = row.get("response", "")
        reasoning = row.get("reasoning_content", "")
        combined_text = f"{reasoning}\n{text}"

        urls = self.extract_urls(combined_text)
        tasks = [asyncio.create_task(self.verify_single_url(session, u)) for u in urls]
        results = await asyncio.gather(*tasks) if tasks else []

        row["verified_links"] = results
        return row


async def main():
    verifier = LinkVerifier()

    if not INPUT_FILE.exists():
        print(f"输入文件不存在: {INPUT_FILE}")
        return

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as session:
        with open(INPUT_FILE, "r", encoding="utf-8") as f, \
                open(OUTPUT_FILE, "w", encoding="utf-8") as out:

            rows = []
            for line in f:
                if not line.strip():
                    continue
                rows.append(json.loads(line))

            def pid_key(pid: str) -> int:
                m = re.search(r"(\d+)", pid or "")
                return int(m.group(1)) if m else 0

            BATCH_SIZE = 400
            results = []
            total = len(rows)
            for start in range(0, total, BATCH_SIZE):
                batch = rows[start:start + BATCH_SIZE]
                tasks = [asyncio.create_task(verifier.process_row(session, row)) for row in batch]
                for i, task in enumerate(asyncio.as_completed(tasks), 1):
                    result_row = await task
                    results.append(result_row)
                    processed = start + i
                    if processed % 20 == 0 or processed == total:
                        print(f"已处理 {processed}/{total} 条（分批并发）")

            results.sort(key=lambda r: (pid_key(r.get("prompt_id", "")), r.get("model", "")))

            for i, result_row in enumerate(results, 1):
                out.write(json.dumps(result_row, ensure_ascii=False) + "\n")
                out.flush()
                if i % 50 == 0 or i == len(results):
                    print(f"已写入 {i}/{len(results)} 条（按 prompt_id 排序）")


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
