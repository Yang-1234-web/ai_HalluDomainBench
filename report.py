# report.py
import json
import csv
from pathlib import Path

# --- 配置区 ---
# 这里保持和你之前一样的路径结构
INPUT_FILE = Path(r"C:\Users\LENOVO\PycharmProjects\PythonProjectAI\data\response\verified_links.jsonl")
OUTPUT_CSV = Path(r"C:\Users\LENOVO\PycharmProjects\PythonProjectAI\data\response\verification_report.csv")


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
                    "详细说明 (Reason)": "-"
                })
                continue

            # 如果提取到了网址，将其拆解展开
            for link in verified_links:
                url = link.get("url", "")
                result = link.get("result", "")
                reason = link.get("reason", "")

                # 状态美化
                status_icon = "存活 (Live)" if result == "live" else "死链 (Dead)"

                table_data.append({
                    "问题编号 (Prompt ID)": prompt_id,
                    "模型名称 (Model)": model,
                    "提取的网址 (URL)": url,
                    "存活状态 (Status)": status_icon,
                    "详细说明 (Reason)": reason
                })

    # 2. 在控制台打印一个简单的预览表格
    print(f"{'模型名称':<25} | {'存活状态':<15} | {'详细说明':<20} | {'网址'}")
    print("-" * 110)
    for d in table_data:
        # 为了控制台排版整洁，截断过长的模型名称
        short_model = d['模型名称 (Model)'][-25:] if len(d['模型名称 (Model)']) > 25 else d['模型名称 (Model)']
        print(
            f"{short_model:<25} | {d['存活状态 (Status)']:<15} | {d['详细说明 (Reason)']:<20} | {d['提取的网址 (URL)']}")

    # 3. 导出为 CSV 文件供 Excel 使用
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    # 使用 utf-8-sig 编码，确保用 Excel 打开时中文不会乱码
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["问题编号 (Prompt ID)", "模型名称 (Model)", "提取的网址 (URL)", "存活状态 (Status)",
                      "详细说明 (Reason)"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()
        writer.writerows(table_data)

    print(f"\n数据处理完成！完整表格已成功导出至: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()