FROM python:3.11-slim

WORKDIR /app

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖（unstructured 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# 使用阿里云镜像源加速
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 复制依赖文件1
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制依赖文件2
COPY requirements2.txt .
RUN pip install --no-cache-dir -r requirements2.txt

# 复制依赖文件3
COPY requirements3.txt .
RUN pip install --no-cache-dir -r requirements3.txt

# 复制依赖文件4
COPY requirements4.txt .
RUN pip install --no-cache-dir -r requirements4.txt

# 复制所有代码
COPY src/ ./src/
COPY .env .env
COPY workspace/ ./workspace/

# 创建数据目录（实际会被挂载覆盖）
RUN mkdir -p /app/data/tbox_docs /app/data/parent_store /app/data/tbox_vector_db \
    /app/data/translate_input /app/data/translate_output

# 暴露 Streamlit 端口
EXPOSE 8501

# 启动命令
CMD ["streamlit", "run", "src/streamlit.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]