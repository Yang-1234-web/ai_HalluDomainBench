#!/usr/bin/env python3
"""
AI模型链接验证系统 - 全流程运行脚本

自动执行以下步骤：
1. 清理旧的输出文件
2. 运行数据收集（collect.py）
3. 运行链接验证（verify2.py）
4. 生成验证报告（report.py）
"""

import subprocess
import sys
import os
from pathlib import Path


def run_command(cmd, description):
    """运行命令并实时显示输出"""
    print(f"[{description}]")
    print(f"执行命令: {' '.join(cmd)}")
    print()
    
    # 直接重定向输出到sys.stdout和sys.stderr
    process = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=os.path.dirname(os.path.abspath(__file__))
    )
    
    process.wait()
    
    if process.returncode != 0:
        print(f"{description} 失败！退出码: {process.returncode}")
        return False
    
    print(f"{description} 成功完成！")
    print()
    return True


def cleanup_old_files():
    """清理旧的输出文件"""
    project_root = Path(__file__).resolve().parent
    output_dir = project_root / "data" / "response"
    
    if output_dir.exists():
        print("清理旧的输出文件...")
        for file in output_dir.iterdir():
            if file.is_file():
                file.unlink()
        print("清理完成！")
        print()
    else:
        # 创建输出目录
        output_dir.mkdir(parents=True, exist_ok=True)


def main():
    """主函数"""
    print("==========================================")
    print("        AI模型链接验证系统 - 全流程运行")
    print("==========================================")
    print()
    
    # 清理旧的输出文件
    cleanup_old_files()
    
    # 步骤1: 运行数据收集
    collect_cmd = [
        sys.executable, "collect.py",
        "--workers", "5",
        "--sleep-sec", "2"
    ]
    if not run_command(collect_cmd, "运行数据收集"):
        return 1
    
    # 步骤2: 运行链接验证
    verify_cmd = [sys.executable, "verify2.py"]
    if not run_command(verify_cmd, "运行链接验证"):
        return 1
    
    # 步骤3: 生成验证报告
    report_cmd = [sys.executable, "report.py"]
    if not run_command(report_cmd, "生成验证报告"):
        return 1
    
    # 完成信息
    print("==========================================")
    print("        全流程运行完成！")
    print("==========================================")
    print()
    
    # 显示生成的文件
    project_root = Path(__file__).resolve().parent
    output_dir = project_root / "data" / "response"
    
    print("生成的文件：")
    print(f"- 数据收集结果: {output_dir / 'model_real_outputs.jsonl'}")
    print(f"- 验证结果: {output_dir / 'verified_links.jsonl'}")
    print(f"- 验证报告: {output_dir / 'verification_report.csv'}")
    print()
    
    print("按回车键退出...")
    input()
    return 0


if __name__ == "__main__":
    sys.exit(main())