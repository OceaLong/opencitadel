import base64
import hashlib
import logging
import re
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from app.domain.utils.secret_masking import mask_secret

logger = logging.getLogger(__name__)

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VERSIONED_PREFIX = "v2"


def _derive_fernet_key(secret: str) -> bytes:
    """从配置密钥派生Fernet密钥"""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class ApiKeyCipherError(Exception):
    """Raised when encrypted API key data cannot be decrypted."""


class ApiKeyCipher:
    """API Key加解密工具"""

    def __init__(
        self,
        secret: str,
        *,
        key_id: str = "primary",
        previous_secrets: Mapping[str, str] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("API_KEY_SECRET 未配置，无法初始化密钥加密器")
        if not _KEY_ID_RE.fullmatch(key_id):
            raise ValueError("API Key key id 必须是 1-64 位字母、数字、下划线或连字符")
        self.current_key_id = key_id
        self._fernet = Fernet(_derive_fernet_key(secret))
        self._fernets = {key_id: self._fernet}
        for previous_key_id, previous_secret in (previous_secrets or {}).items():
            if not _KEY_ID_RE.fullmatch(previous_key_id):
                raise ValueError(f"无效的历史 API Key key id: {previous_key_id}")
            if not previous_secret:
                raise ValueError(f"历史 API Key key id[{previous_key_id}]未配置密钥")
            if previous_key_id == key_id and previous_secret != secret:
                raise ValueError("当前 key id 不能映射到不同的历史密钥")
            self._fernets[previous_key_id] = Fernet(_derive_fernet_key(previous_secret))

    def encrypt_versioned(self, plain: str) -> str:
        if not plain:
            return ""
        token = self._fernet.encrypt(plain.encode()).decode()
        return f"{_VERSIONED_PREFIX}.{self.current_key_id}.{token}"

    def decrypt_versioned(self, encrypted: str) -> str:
        if not encrypted:
            return ""
        try:
            prefix, key_id, token = encrypted.split(".", 2)
        except ValueError as exc:
            raise ApiKeyCipherError("无效的版本化 API Key 密文") from exc
        if prefix != _VERSIONED_PREFIX or not _KEY_ID_RE.fullmatch(key_id):
            raise ApiKeyCipherError("无效的版本化 API Key 密文")
        fernet = self._fernets.get(key_id)
        if fernet is None:
            raise ApiKeyCipherError(f"未知或已移除的 API Key key id: {key_id}")
        try:
            return fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ApiKeyCipherError(f"无法使用 API Key key id[{key_id}]解密凭据") from exc

    @staticmethod
    def key_id_from_ciphertext(encrypted: str) -> str | None:
        try:
            prefix, key_id, _ = encrypted.split(".", 2)
        except ValueError:
            return None
        if prefix != _VERSIONED_PREFIX or not _KEY_ID_RE.fullmatch(key_id):
            return None
        return key_id

    @staticmethod
    def mask(api_key: str) -> str:
        return mask_secret(api_key)
