# 使用 python 官方基础镜像
FROM python:3.11-bookworm

# 设置工作目录
WORKDIR /app

# 将 requirements.txt 拷贝到容器中
COPY requirements.txt .

# 安装 Python 依赖，再使用 playwright CLI 安装内置浏览器以及其系统级的依赖
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

# 将项目的核心代码拷贝进容器
COPY . .

# 暴露挂载目录，便于外部持久化 PDF 和历史记录
VOLUME ["/app/pdf_exports", "/app/daily_reports"]

# 在容器启动时执行的主程序
CMD ["python", "continuous_tracker.py"]