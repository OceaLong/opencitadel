# 1.确认基础镜像源
FROM python:3.12-slim AS base

# 2.配置容器工作空间
WORKDIR /app

# 3.安装curl, 便于后续使用curl安装nodejs(Playwrigh需要安装nodejs作为中转连接)
RUN apt-get update && apt-get install -y curl

# 4.安装v22版本的node.js
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs

# 5.安装uv, 使用uv来同步依赖环境
RUN pip install uv

# 6.配置uv使用阿里云镜像源
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 7.拷贝依赖清单并安装依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 8.拷贝项目文件(忽略开发环境的库)
COPY . .

# 9.为运行脚本配置权限
RUN chmod +x run.sh worker.sh migrate.sh

# 10.设置Python路径变量
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

# 10.1 禁用Python输出缓冲，确保日志实时输出到docker logs
ENV PYTHONUNBUFFERED=1

# ==================== API 镜像 ====================
FROM base AS api

ENV MANUS_PROCESS_ROLE=api

# 11.暴露端口号
EXPOSE 8000

# 12.启动命令脚本
CMD ["./run.sh"]

# ==================== Worker 镜像 ====================
FROM base AS worker

ENV MANUS_PROCESS_ROLE=worker

CMD ["./worker.sh"]
