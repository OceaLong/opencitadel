# 1.确认基础镜像源
FROM python:3.12-slim AS base

# 构建期镜像源与超时（可通过 docker compose build args 覆盖）
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_RETRIES=5
ARG UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG UV_VERSION=0.11.33
ARG UV_HTTP_TIMEOUT=300
ARG PYPI_FILES_MIRROR=https://mirrors.aliyun.com/pypi/packages/
# 是否安装 kubernetes SDK(k8s extra)。发布/CI 镜像默认 1(全功能,Helm/K8s 部署可用);
# 本地 docker-compose 构建传 0(本地用 docker 驱动,不需要 kubernetes 包)。
ARG WITH_K8S=1

ENV UV_INDEX_URL=${UV_INDEX_URL}
ENV UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT}

# 2.配置容器工作空间
WORKDIR /app

# 3.安装健康检查所需的 curl。浏览器运行在隔离沙箱中。
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 4.安装uv, 使用uv来同步依赖环境（pip 走国内镜像，避免 files.pythonhosted.org 超时）
RUN pip install --no-cache-dir \
    -i "${PIP_INDEX_URL}" \
    --trusted-host "${PIP_TRUSTED_HOST}" \
    --default-timeout="${PIP_DEFAULT_TIMEOUT}" \
    --retries="${PIP_RETRIES}" \
    "uv==${UV_VERSION}"

# 5.拷贝依赖清单并安装依赖（执行内核专用重库在 execution-kernel target 单独安装）
COPY pyproject.toml uv.lock ./
RUN if [ -n "${PYPI_FILES_MIRROR}" ]; then \
      sed -i "s|https://files.pythonhosted.org/packages/|${PYPI_FILES_MIRROR}|g" uv.lock; \
    fi
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project $([ "$WITH_K8S" = "1" ] && echo "--extra k8s")

# 6.拷贝项目文件(忽略开发环境的库)
COPY . .

# 7.为运行脚本配置权限
RUN chmod +x run.sh execution-kernel.sh migrate.sh

# 8.设置Python路径变量
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

# 8.1 禁用Python输出缓冲，确保日志实时输出到docker logs
ENV PYTHONUNBUFFERED=1

# 8.2 创建非 root 用户(uid 与 Helm runAsUser 保持一致);USER 切换下放到各 target 末尾，
#     因为 execution-kernel target 还需以可写权限安装执行依赖组
RUN useradd --system --uid 10001 --create-home --home-dir /home/appuser \
    --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

# ==================== API 镜像 ====================
FROM base AS api

ENV OPENCITADEL_PROCESS_ROLE=api

# 9.暴露端口号
EXPOSE 8000

# 10.以非 root 用户运行
USER appuser

# 11.启动命令脚本
CMD ["./run.sh"]

# ==================== Execution Kernel 镜像 ====================
FROM base AS execution-kernel

# ARG 为 stage 作用域,不会跨 FROM 边界继承自 base stage,需在此重新声明
ARG WITH_K8S=1

ENV OPENCITADEL_PROCESS_ROLE=execution-kernel

# 9.安装执行内核专用重库(tree-sitter-language-pack/playwright/jieba/python-pptx/openpyxl/markdownify/mcp)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --group execution-kernel $([ "$WITH_K8S" = "1" ] && echo "--extra k8s")

# 10.以非 root 用户运行
USER appuser

# 11.启动命令脚本
CMD ["./execution-kernel.sh"]
