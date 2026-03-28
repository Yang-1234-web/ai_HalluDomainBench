@echo off

REM 设置颜色
echo [92m==========================================[0m
echo        AI模型链接验证系统 - 全流程运行
 echo ==========================================[0m
echo.

REM 检查Python环境
echo [93m[1/4] 检查Python环境...[0m
echo 正在使用虚拟环境: ai_security
echo.

REM 运行数据收集
echo [93m[2/4] 运行数据收集...[0m
echo 正在向AI模型发送测试问题...
echo 命令: python collect.py --workers 5 --sleep-sec 2
echo.
python collect.py --workers 5 --sleep-sec 2

if %errorlevel% neq 0 (
    echo [91m数据收集失败！[0m
    pause
    exit /b %errorlevel%
)
echo [92m数据收集成功完成！[0m
echo.

REM 运行链接验证
echo [93m[3/4] 运行链接验证...[0m
echo 正在验证AI提供的链接...
echo 命令: python verify2.py
echo.
python verify2.py

if %errorlevel% neq 0 (
    echo [91m链接验证失败！[0m
    pause
    exit /b %errorlevel%
)
echo [92m链接验证成功完成！[0m
echo.

REM 运行报告生成
echo [93m[4/4] 生成验证报告...[0m
echo 正在生成CSV报告...
echo 命令: python report.py
echo.
python report.py

if %errorlevel% neq 0 (
    echo [91m报告生成失败！[0m
    pause
    exit /b %errorlevel%
)
echo [92m报告生成成功完成！[0m
echo.

echo [92m==========================================[0m
echo        全流程运行完成！
echo ==========================================[0m
echo.
echo 生成的文件：
echo - 数据收集结果: data\response\model_real_outputs.jsonl
echo - 验证结果: data\response\verified_links.jsonl
echo - 验证报告: data\response\verification_report.csv
echo.
echo [93m按任意键退出...[0m
pause >nul
exit /b 0