#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import secrets
from dataclasses import dataclass


SERVICE_KEY_PREFIX = "sk"


@dataclass(frozen=True)
class GeneratedServiceApiKey:
    plaintext: str
    key_hash: str
    prefix: str


class ServiceApiKeyHasher:
    def generate(self) -> GeneratedServiceApiKey:
        secret = secrets.token_urlsafe(32)
        plaintext = f"{SERVICE_KEY_PREFIX}-{secret}"
        return GeneratedServiceApiKey(
            plaintext=plaintext,
            key_hash=self.hash(plaintext),
            prefix=plaintext[:12],
        )

    def hash(self, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
