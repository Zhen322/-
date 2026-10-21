# Windows 启动脚本：双击或在 PowerShell 中运行 .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 检查 Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 python，请先安装 Python 3.9+：https://www.python.org/downloads/" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# 安装 Pillow（如缺失）
python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装 Pillow…" -ForegroundColor Yellow
    python -m pip install Pillow
}

$port = 8766
Write-Host "启动跨平台新媒体助手： http://127.0.0.1:$port" -ForegroundColor Green
Write-Host "首次使用请在【设置】中填入 DeepSeek API Key；在【发布管理】配置平台凭据。"
python app.py --port $port
