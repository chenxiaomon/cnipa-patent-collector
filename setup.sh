#!/bin/bash

# Patent Collector 依赖安装脚本

echo "📦 安装项目依赖..."

# 检查 Python 版本
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python 版本: $python_version"

# 检查是否有虚拟环境
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  建议在虚拟环境中安装依赖"
    echo "创建虚拟环境: python3 -m venv venv"
    echo "激活虚拟环境: source venv/bin/activate"
    read -p "是否继续? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 升级 pip
echo "🔄 升级 pip..."
pip install --upgrade pip

# 安装依赖
echo "📥 安装项目依赖..."
pip install -r requirements.txt

echo "✅ 依赖安装完成！"
echo "运行程序: python main_automation.py"
