# report.py
import json
import csv
from pathlib import Path

# --- 配置区 ---
# 使用项目相对路径
ROOT = Path(__file__).resolve().parents[0]
INPUT_FILE = ROOT / "data" / "response" / "verified_links.jsonl"
OUTPUT_CSV = ROOT / "data" / "response" / "verification_report.csv"
OUTPUT_DEAD_CSV = ROOT / "data" / "response" / "verification_report_dead.csv"


def main():
    if not INPUT_FILE.exists():
        print(f" 找不到输入文件: {INPUT_FILE}")
        return

    table_data = []

    # 1. 读取 JSONL 并解析提取数据
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)

            model = row.get("model", "Unknown")
            prompt_id = row.get("prompt_id", "Unknown")
            verified_links = row.get("verified_links", [])

            # 如果 AI 在回答中一个网址都没给
            if not verified_links:
                table_data.append({
                    "问题编号 (Prompt ID)": prompt_id,
                    "模型名称 (Model)": model,
                    "提取的网址 (URL)": "未提取到网址",
                    "存活状态 (Status)": "-",
                    "状态码/原因 (Code/Detail)": "-"
                })
                continue

            # 如果提取到了网址，将其拆解展开
            for link in verified_links:
                url = link.get("url", "")
                result = link.get("result", "")
                reason = link.get("reason", "")

                # 状态美化
                if result == "live":
                    status_icon = "存活 (Live)"
                elif result == "unknown":
                    status_icon = "未知 (Unknown)"
                else:
                    status_icon = "死链 (Dead)"

                table_data.append({
                    "问题编号 (Prompt ID)": prompt_id,
                    "模型名称 (Model)": model,
                    "提取的网址 (URL)": url,
                    "存活状态 (Status)": status_icon,
                    "状态码/原因 (Code/Detail)": reason if reason else "-"
                })

    # 2. 在控制台打印一个简单的预览表格
    print(f"{'模型名称':<25} | {'存活状态':<15} | {'状态码/原因':<25} | {'网址'}")
    print("-" * 110)
    for d in table_data:
        # 为了控制台排版整洁，截断过长的模型名称
        short_model = d['模型名称 (Model)'][-25:] if len(d['模型名称 (Model)']) > 25 else d['模型名称 (Model)']
        print(
            f"{short_model:<25} | {d['存活状态 (Status)']:<15} | {d['状态码/原因 (Code/Detail)']:<25} | {d['提取的网址 (URL)']}")

    # 3. 导出为 CSV 文件供 Excel 使用
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    # 使用 utf-8-sig 编码，确保用 Excel 打开时中文不会乱码；用制表符分隔，URL 后无逗号，便于点击/复制
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["问题编号 (Prompt ID)", "模型名称 (Model)", "提取的网址 (URL)", "存活状态 (Status)",
                      "状态码/原因 (Code/Detail)"]
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(table_data)

    # 导出所有死链
    dead_rows = [
        row for row in table_data
        if row.get("存活状态 (Status)", "").startswith("死链")
    ]
    with open(OUTPUT_DEAD_CSV, "w", encoding="utf-8-sig", newline="") as f_dead:
        writer = csv.DictWriter(
            f_dead,
            fieldnames=["问题编号 (Prompt ID)", "模型名称 (Model)", "提取的网址 (URL)", "存活状态 (Status)", "状态码/原因 (Code/Detail)"],
            delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(dead_rows)

    print(f"\n数据处理完成！已导出:\n- 全部: {OUTPUT_CSV}（制表符分隔，URL 后无逗号）\n- 死链: {OUTPUT_DEAD_CSV}")


if __name__ == "__main__":
    main()
