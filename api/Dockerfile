# 1.确认基础镜像源
FROM python:3.12-slim AS base

# 构建期镜像源与超时（可通过 docker compose build args 覆盖）
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_RETRIES=5
ARG UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG UV_VERSION=0.11.19
ARG UV_HTTP_TIMEOUT=300
ARG PYPI_FILES_MIRROR=https://mirrors.aliyun.com/pypi/packages/

ENV UV_INDEX_URL=${UV_INDEX_URL}
ENV UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT}

# 2.配置容器工作空间
WORKDIR /app

# 3.安装curl, 便于后续使用curl安装nodejs(Playwrigh需要安装nodejs作为中转连接)
RUN apt-get update && apt-get install -y curl

# 4.安装v22版本的node.js
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs

# 5.安装uv, 使用uv来同步依赖环境（pip 走国内镜像，避免 files.pythonhosted.org 超时）
RUN pip install --no-cache-dir \
    -i "${PIP_INDEX_URL}" \
    --trusted-host "${PIP_TRUSTED_HOST}" \
    --default-timeout="${PIP_DEFAULT_TIMEOUT}" \
    --retries="${PIP_RETRIES}" \
    "uv==${UV_VERSION}"

# 6.拷贝依赖清单并安装依赖
COPY pyproject.toml uv.lock ./
RUN if [ -n "${PYPI_FILES_MIRROR}" ]; then \
      sed -i "s|https://files.pythonhosted.org/packages/|${PYPI_FILES_MIRROR}|g" uv.lock; \
    fi
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 7.拷贝项目文件(忽略开发环境的库)
COPY . .

# 8.为运行脚本配置权限
RUN chmod +x run.sh worker.sh migrate.sh

# 9.设置Python路径变量
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

# 9.1 禁用Python输出缓冲，确保日志实时输出到docker logs
ENV PYTHONUNBUFFERED=1

# ==================== API 镜像 ====================
FROM base AS api

ENV OPENCITADEL_PROCESS_ROLE=api

# 10.暴露端口号
EXPOSE 8000

# 11.启动命令脚本
CMD ["./run.sh"]

# ==================== Worker 镜像 ====================
FROM base AS worker

ENV OPENCITADEL_PROCESS_ROLE=worker

CMD ["./worker.sh"]
