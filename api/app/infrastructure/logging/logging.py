import logging
import sys

from core.config import DeploymentSettings


def setup_logging(settings: DeploymentSettings) -> None:
    """配置 OpenCitadel 项目的日志系统，涵盖日志等级、输出格式、输出渠道等"""
    # 1.获取根日志处理器
    root_logger = logging.getLogger()

    # 2.清除已有的handlers，避免uvicorn的dictConfig重配置后产生冲突或重复
    root_logger.handlers.clear()

    # 3.设置根日志处理器等级
    log_level = getattr(logging, settings.log_level)
    root_logger.setLevel(log_level)

    # 4.日志输出格式定义
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 5.创建控制台日志输出处理器(使用stderr，stderr在Python中始终无缓冲，Docker中更可靠)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # 6.将控制台日志处理器添加到根日志处理器中
    root_logger.addHandler(console_handler)

    # 7.确保应用与 uvicorn 相关 logger 可正常输出
    for logger_name in ("app", "uvicorn", "uvicorn.error", "uvicorn.access"):
        named_logger = logging.getLogger(logger_name)
        named_logger.setLevel(log_level)
        named_logger.propagate = True
        named_logger.disabled = False

    from app.infrastructure.observability.logging_context import configure_structured_logging

    configure_structured_logging(log_format=settings.log_format)
    root_logger.info("日志系统初始化完成")
