"""containment 与 sudo 分支注入相关的负向测试。

这些用例针对侦察确认的真实缺陷编写：
- `_normalize_filepath` 曾经零 containment，`../` 与任意绝对路径均放行；
- `find_files/ensure_file/check_file_exists/delete_file` 曾完全绕过归一化；
- `write_file` sudo 分支曾用未引号的f-string拼接filepath（shell注入）；
- `read_file` sudo 分支曾用可被单引号逃逸的拼接方式。

在修复前运行本文件，多数用例应为 FAIL；修复后应全绿。
"""

import asyncio
import os
import shlex

import pytest
from anyio import Path as AsyncPath
from app.interfaces.errors.exceptions import BadRequestException
from app.services.file import FileService

ESCAPE_PATH = "../../etc/passwd"


class _FakeProcess:
    """替代asyncio子进程对象，避免测试真正执行sudo命令。"""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


# ---------------------------------------------------------------------------
# containment：拒绝逃逸路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    ["../../etc/passwd", "/etc/shadow", "a/../../../etc/hosts"],
)
async def test_read_file_rejects_escape(sandbox_home, evil):
    with pytest.raises(BadRequestException):
        await FileService.read_file(evil)


async def test_symlink_escape_rejected(sandbox_home, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (sandbox_home / "link.txt").symlink_to(outside)

    with pytest.raises(BadRequestException):
        await FileService.read_file("link.txt")  # realpath 落在 home 外


async def test_write_file_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService.write_file(ESCAPE_PATH, "data")


async def test_legit_path_within_home_is_allowed(sandbox_home):
    result = await FileService.write_file("nested/report.md", "hello")
    assert result.filepath == str(sandbox_home / "nested" / "report.md")

    read_result = await FileService.read_file("nested/report.md")
    assert read_result.content == "hello"
    assert read_result.truncated is False
    assert read_result.size_bytes == 5


async def test_read_file_reports_truncation_as_structured_metadata(sandbox_home):
    await FileService.write_file("oversized.txt", "hello")

    result = await FileService.read_file("oversized.txt", max_length=1)

    assert result.content == "h(truncated)"
    assert result.truncated is True
    assert result.size_bytes == 5


# ---------------------------------------------------------------------------
# 此前完全绕过归一化的4个方法，逐一断言"../x"式路径被拒绝
# ---------------------------------------------------------------------------


async def test_find_files_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService.find_files(ESCAPE_PATH, "*")


async def test_ensure_file_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService.ensure_file(ESCAPE_PATH)


async def test_check_file_exists_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService.check_file_exists(ESCAPE_PATH)


async def test_delete_file_rejects_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService().delete_file(ESCAPE_PATH)


# ---------------------------------------------------------------------------
# sudo 分支：恶意 filepath 不能作为独立 shell 词元出现
# ---------------------------------------------------------------------------


async def test_sudo_write_quotes_filepath(sandbox_home, monkeypatch):
    captured = {}

    async def fake_exec(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_exec)

    malicious = 'x"; touch /tmp/pwned; echo "'
    await FileService.write_file(malicious, "data", sudo=True)

    cmd = captured["cmd"]
    expected_filepath = FileService._normalize_filepath(malicious)
    expected_quoted = shlex.quote(expected_filepath)

    # 恶意串必须整体出现在一个被shlex.quote包裹的词元里
    assert expected_quoted in cmd
    # 去掉这个安全词元后，注入的命令片段不应再作为可执行的独立命令出现
    assert "touch /tmp/pwned" not in cmd.replace(expected_quoted, "", 1)


# ---------------------------------------------------------------------------
# sudo write 的临时文件清理须在 finally 中：子进程非0退出（抛异常）时也不能残留（M4）。
# ---------------------------------------------------------------------------


async def test_sudo_write_cleans_temp_file_even_on_failure(sandbox_home, monkeypatch):
    async def fake_exec_failure(cmd, **kwargs):
        return _FakeProcess(returncode=1, stderr=b"boom")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_exec_failure)

    temp_file = f"/tmp/file_write_{os.getpid()}.tmp"
    temp_path = AsyncPath(temp_file)
    # 保证测试开始前环境干净，避免与其它测试/进程遗留文件互相干扰
    if await temp_path.exists():
        await temp_path.unlink()

    with pytest.raises(BadRequestException):
        await FileService.write_file("report.md", "data", sudo=True)

    assert not await temp_path.exists(), "sudo写入失败后临时文件应被finally清理，不应残留"


# ---------------------------------------------------------------------------
# find_files 的目录参数须走"目录规则"：末尾斜杠/相对路径/'.' 均应正常解析，
# 而不是被文件路径的 basename in ("", ".", "..") 规则误伤（C1）。
# ---------------------------------------------------------------------------


async def test_find_files_accepts_relative_subdir_with_trailing_slash(sandbox_home):
    (sandbox_home / "sub").mkdir()
    (sandbox_home / "sub" / "a.txt").write_text("x")

    result = await FileService.find_files("sub/", "*.txt")

    assert result.dir_path == str(sandbox_home / "sub")
    assert any(f.endswith("a.txt") for f in result.files)


async def test_find_files_accepts_home_dir_with_trailing_slash(sandbox_home):
    (sandbox_home / "report.md").write_text("x")

    result = await FileService.find_files(f"{sandbox_home}/", "*.md")

    assert result.dir_path == str(sandbox_home)
    assert any(f.endswith("report.md") for f in result.files)


async def test_find_files_accepts_dot_as_home(sandbox_home):
    (sandbox_home / "report.md").write_text("x")

    result = await FileService.find_files(".", "*.md")

    assert result.dir_path == str(sandbox_home)
    assert any(f.endswith("report.md") for f in result.files)


async def test_find_files_rejects_relative_dotdot_escape(sandbox_home):
    with pytest.raises(BadRequestException):
        await FileService.find_files("../", "*")


async def test_sudo_read_quotes_filepath(sandbox_home, monkeypatch):
    captured = {}

    async def fake_exec(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(returncode=0, stdout=b"content")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_exec)

    # 内含单引号，旧实现f"sudo cat '{filepath}'"会被这个单引号提前闭合
    malicious = "x'; touch /tmp/pwned; echo '"
    await FileService.read_file(malicious, sudo=True)

    cmd = captured["cmd"]
    expected_filepath = FileService._normalize_filepath(malicious)
    expected_quoted = shlex.quote(expected_filepath)

    assert expected_quoted in cmd
    assert "touch /tmp/pwned" not in cmd.replace(expected_quoted, "", 1)
