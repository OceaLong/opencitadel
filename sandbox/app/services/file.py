import asyncio
import glob
import logging
import os.path
import re
import shlex

from anyio import Path as AsyncPath
from fastapi import UploadFile

from app.interfaces.errors.exceptions import AppException, BadRequestException, NotFoundException
from app.models.file import (
    FileCheckResult,
    FileDeleteResult,
    FileFindResult,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileUploadResult,
    FileWriteResult,
)

logger = logging.getLogger(__name__)

SANDBOX_HOME_DIR = "/home/ubuntu"

# 允许访问的根目录白名单，规范化后的路径必须落在其中之一才被放行。
# 测试可通过 monkeypatch 收紧/替换该常量（例如指向临时目录），生产环境不应放宽。
SANDBOX_ALLOWED_ROOTS = ("/home/ubuntu", "/tmp", "/workspace")


class FileService:
    """文件沙箱服务"""

    def __init__(self) -> None:
        pass

    @classmethod
    def _is_within_allowed_roots(cls, resolved_path: str) -> bool:
        """判断解析后的规范路径(realpath)是否落在允许根目录白名单内。"""
        for root in SANDBOX_ALLOWED_ROOTS:
            root_resolved = os.path.realpath(root)
            try:
                common = os.path.commonpath([resolved_path, root_resolved])
            except ValueError:
                continue
            if common == root_resolved:
                return True
        return False

    @classmethod
    def _normalize_filepath(cls, filepath: str) -> str:
        """规范化文件路径，兼容相对路径并确保包含目录部分，同时进行 containment 校验。"""
        normalized = (filepath or "").strip()
        if not normalized:
            raise BadRequestException("文件路径不能为空")
        if not normalized.startswith("/"):
            normalized = f"{SANDBOX_HOME_DIR}/{normalized.lstrip('/')}"
        if os.path.basename(normalized) in ("", ".", ".."):
            raise BadRequestException(f"无效的文件路径: {filepath}")
        directory = os.path.dirname(normalized)
        if not directory:
            raise BadRequestException(f"无效的文件路径: {filepath}")

        # containment 校验：解析符号链接/`..`后的规范路径必须落在允许根目录之内，
        # 参照 api 侧 source_validator.py 的 realpath + commonpath 手法。
        resolved = os.path.realpath(normalized)
        if not cls._is_within_allowed_roots(resolved):
            raise BadRequestException("path outside sandbox allowed roots")

        return normalized

    @classmethod
    def _normalize_dirpath(cls, dirpath: str) -> str:
        """规范化目录路径，兼容相对路径/末尾斜杠/`.`，同时进行 containment 校验。

        与 `_normalize_filepath` 的区别：目录场景下 `''`/`.`/`..` 均是合法的目录
        basename（例如末尾带斜杠的 `sub/`、代表 home 的 `.`），不应像文件路径那样
        直接拒绝——否则会误伤 LLM 高频写法（`'sub/'`、`'<home>/'`、`'.'`）。这里改用
        `os.path.normpath` 收敛末尾斜杠/单点分量，逃逸（如 `'../'`）仍由下方的
        realpath + containment 校验兜底拒绝，语义与 `_normalize_filepath` 一致。
        """
        normalized = (dirpath or "").strip()
        if not normalized:
            raise BadRequestException("目录路径不能为空")
        if not normalized.startswith("/"):
            normalized = f"{SANDBOX_HOME_DIR}/{normalized.lstrip('/')}"
        normalized = os.path.normpath(normalized)

        # containment 校验：与 _normalize_filepath 保持一致的 realpath + commonpath 手法。
        resolved = os.path.realpath(normalized)
        if not cls._is_within_allowed_roots(resolved):
            raise BadRequestException("path outside sandbox allowed roots")

        return normalized

    @classmethod
    async def read_file(
        cls,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        sudo: bool = False,
        max_length: int | None = 10000,
    ) -> FileReadResult:
        """根据传递的文件路径+起始行号+权限+最大长度读取文件内容"""
        try:
            filepath = cls._normalize_filepath(filepath)

            # 1.检测在当前权限下能否获取该文件
            if not await AsyncPath(filepath).exists() and not sudo:
                logger.error("要读取的文件不存在或无权限: %s", filepath)
                raise NotFoundException(f"要读取的文件不存在或无权限: {filepath}")

            # 2.ubuntu系统下统一使用utf-8编码
            encoding = "utf-8"

            # 3.判断是否为sudo，如果是sudo系统则使用命令行的形式读取文件
            if sudo:
                # 4.使用sudo cat命令读取文件内容（filepath经shlex.quote转义,防止shell注入）
                command = f"sudo cat {shlex.quote(filepath)}"
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # 5.读取子进程的输出，并等待子进程结束
                stdout, stderr = await process.communicate()

                # 6.判断子进程的状态是否正常结束
                if process.returncode != 0:
                    raise BadRequestException(f"阅读文件失败: {stderr.decode()}")

                # 7.读取输出内容
                content = stdout.decode(encoding, errors="replace")
            else:
                # 8.创建一个内部读取函数
                def async_read_file() -> str:
                    try:
                        with open(filepath, encoding=encoding) as f:
                            return f.read()
                    except (OSError, RuntimeError, ValueError) as async_read_file_exception:
                        raise AppException(
                            msg=f"读取文件失败: {async_read_file_exception!s}"
                        ) from async_read_file_exception

                # 9.使用asyncio创建线程读取文件
                content = await asyncio.to_thread(async_read_file)

            # 10.判断是否传递了读取范围
            if start_line is not None or end_line is not None:
                # 11.将内容切割成行，并且提取指定范围行号的数据
                lines = content.splitlines()
                start = start_line if start_line is not None else 0
                end = end_line if end_line is not None else len(lines)
                content = "\n".join(lines[start:end])

            size_bytes = len(content.encode(encoding, errors="replace"))
            truncated = max_length is not None and 0 < max_length < len(content)
            # 12.裁切下数据长度
            if truncated:
                content = content[:max_length] + "(truncated)"

            return FileReadResult(
                filepath=filepath,
                content=content,
                truncated=truncated,
                size_bytes=size_bytes,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # 13.判断异常类型执行不同操作
            if isinstance(e, (BadRequestException, AppException)):
                raise
            raise AppException(f"文件读取失败: {e!s}") from e

    @classmethod
    async def write_file(
        cls,
        filepath: str,
        content: str,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        sudo: bool = False,
    ) -> FileWriteResult:
        """根据传递的文件路径+内容向指定文件写入内容"""
        try:
            filepath = cls._normalize_filepath(filepath)

            # 1.组装实际写入的内容
            if leading_newline:
                content = "\n" + content
            if trailing_newline:
                content = content + "\n"

            # 2.判断是否是sudo权限，如果是则使用命令行的形式先写入一个缓存文件，然后将缓存文件覆盖原始文件
            if sudo:
                # 4.创建一个临时文件
                temp_file = f"/tmp/file_write_{os.getpid()}.tmp"

                # 5.创建一个内部函数使用asyncio创建新线程写入数据
                def async_write_temp_file() -> int:
                    with open(temp_file, "w", encoding="utf-8") as f:
                        f.write(content)
                    return len(content.encode("utf-8"))

                # 6.使用asyncio创建子线程并写入
                bytes_written = await asyncio.to_thread(async_write_temp_file)

                # 7.使用sudo tee将临时文件内容写入目标文件（filepath/temp_file均经shlex.quote转义,
                #   避免像旧版"sudo bash -c \"...{filepath}...\""那样的嵌套引号被恶意路径逃逸）
                # 临时文件的清理放入finally：即便子进程非0退出(抛异常)也不能残留临时文件(M4)。
                try:
                    tee_flag = " -a" if append else ""
                    command = (
                        f"sudo tee{tee_flag} {shlex.quote(filepath)} "
                        f"< {shlex.quote(temp_file)} > /dev/null"
                    )
                    process = await asyncio.create_subprocess_shell(
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

                    # 8.等待子进程执行完毕
                    _stdout, stderr = await process.communicate()

                    # 9.检测子进程是否正常执行
                    if process.returncode != 0:
                        raise BadRequestException(f"文件内容写入失败: {stderr.decode()}")
                finally:
                    # 10.清除下临时文件
                    temp_path = AsyncPath(temp_file)
                    if await temp_path.exists():
                        await temp_path.unlink()
            else:
                # 11.非sudo使用Python方式写入，先确保文件路径存在
                await AsyncPath(filepath).parent.mkdir(parents=True, exist_ok=True)

                # 12.创建一个异步写入的函数
                def async_write_file() -> int:
                    write_mode = "a" if append else "w"
                    with open(filepath, write_mode, encoding="utf-8") as f:
                        return f.write(content)

                # 13.使用asyncio创建一个子线程写入内容
                bytes_written = await asyncio.to_thread(async_write_file)

            return FileWriteResult(
                filepath=filepath,
                bytes_written=bytes_written,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # 14.根据不同的错误执行不同的操作
            logger.error("文件内容写入失败: %s", e)
            if isinstance(e, BadRequestException):
                raise
            raise AppException(f"文件内容写入失败: {e!s}") from e

    async def replace_in_file(
        self,
        filepath: str,
        old_str: str,
        new_str: str,
        sudo: bool = False,
    ) -> FileReplaceResult:
        """根据传递的数据替换文件内指定的内容"""
        filepath = self._normalize_filepath(filepath)
        # 1.调用服务获取对应的文件内容
        file_read_result = await self.read_file(filepath=filepath, sudo=sudo, max_length=None)
        content = file_read_result.content

        # 2.计算old_str出现的次数，只有出现次数>0才需要替换
        replaced_count = content.count(old_str)
        if replaced_count == 0:
            return FileReplaceResult(filepath=filepath, replaced_count=replaced_count)

        # 3.替换旧内容
        new_content = content.replace(old_str, new_str)

        # 4.将替换后的新内容写入到文件中
        await self.write_file(
            filepath=filepath,
            content=new_content,
            sudo=sudo,
        )

        return FileReplaceResult(filepath=filepath, replaced_count=replaced_count)

    async def search_in_file(
        self,
        filepath: str,
        regex: str,
        sudo: bool = False,
    ) -> FileSearchResult:
        """根据传递的文件路径+匹配规则查询文件内符合的内容"""
        filepath = self._normalize_filepath(filepath)

        # 1.调用服务获取对应的文件内容
        file_read_result = await self.read_file(filepath=filepath, sudo=sudo, max_length=None)
        content = file_read_result.content

        # 2.将读取的内容拆分成每一行
        lines = content.splitlines()
        matches = []
        line_numbers = []

        # 3.将外部传递的regex转换为正则
        try:
            pattern = re.compile(regex)
        except (OSError, RuntimeError, ValueError) as e:
            raise BadRequestException(f"传递正则表达式[{regex}]出错: {e!s}") from e

        # 4.创建一个异步函数，使用子线程方式执行避免长时间io阻塞
        def async_matches():
            nonlocal matches, line_numbers
            for idx, line in enumerate(lines):
                if pattern.match(line):
                    matches.append(line)
                    line_numbers.append(idx)

        # 5.使用asyncio创建子线程并调用
        await asyncio.to_thread(async_matches)

        return FileSearchResult(
            filepath=filepath,
            matches=matches,
            line_numbers=line_numbers,
        )

    @classmethod
    async def find_files(cls, dir_path: str, glob_pattern: str) -> FileFindResult:
        """根据传递的文件夹路径+glob规则查询文件列表"""
        # 0.归一化目录路径并进行containment校验（走目录规则，而非文件路径的basename拒绝规则）
        dir_path = cls._normalize_dirpath(dir_path)

        # 1.检测下传递进来的目录是否存在
        if not await AsyncPath(dir_path).exists():
            raise NotFoundException(f"当前文件夹不存在: {dir_path}")

        # 2.定义一个异步函数使用asyncio子线程运行避免IO阻塞
        def async_glob():
            search_pattern = os.path.join(dir_path, glob_pattern)
            return glob.glob(search_pattern, recursive=True)

        # 3.创建子线程完成任务
        files = await asyncio.to_thread(async_glob)

        return FileFindResult(dir_path=dir_path, files=files)

    @classmethod
    async def upload_file(cls, file: UploadFile, filepath: str) -> FileUploadResult:
        """根据传递的文件源+路径将文件上传至沙箱"""
        try:
            filepath = cls._normalize_filepath(filepath)

            # 1.定义分块上传，每次只上传8k
            chunk_size = 1024 * 8
            file_size = 0

            # 2.确保上传文件所在的目录存在
            await AsyncPath(filepath).parent.mkdir(parents=True, exist_ok=True)

            # 3.定义一个异步函数用于上传文件避免阻塞进程
            def async_write_file():
                nonlocal file_size
                with open(filepath, "wb") as f:
                    while True:
                        chunk = file.file.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        file_size += len(chunk)

            # 4.使用asyncio子线程完成函数调用
            await asyncio.to_thread(async_write_file)

            return FileUploadResult(
                filepath=filepath,
                file_size=file_size,
                success=True,
            )
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("上传文件到沙箱出错: %s", e)
            raise AppException(f"上传文件到沙箱出错: {e!s}") from e

    @classmethod
    async def ensure_file(cls, filepath: str) -> None:
        """传递filepath用于确保当前文件存在"""
        filepath = cls._normalize_filepath(filepath)
        if not await AsyncPath(filepath).exists():
            raise NotFoundException(f"该文件不存在: {filepath}")

    @classmethod
    async def check_file_exists(cls, filepath: str) -> FileCheckResult:
        """根据传递的路径判断文件是否存在"""
        filepath = cls._normalize_filepath(filepath)
        return FileCheckResult(
            filepath=filepath,
            exists=await AsyncPath(filepath).exists(),
        )

    async def delete_file(self, filepath: str) -> FileDeleteResult:
        """根据传递的路径+sudo删除指定文件"""
        # 0.归一化路径并进行containment校验
        filepath = self._normalize_filepath(filepath)

        # 1.判断文件是否存在
        await self.ensure_file(filepath)

        try:
            # 2.调用命令删除文件
            os.remove(filepath)
            return FileDeleteResult(filepath=filepath, deleted=True)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("删除文件%s失败: %s", filepath, e)
            raise AppException(f"删除文件{filepath}失败: {e!s}") from e
