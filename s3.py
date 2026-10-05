"""Хранилище клипов голов (ADR-030, шаг 6): S3 API Timeweb Cloud, только stdlib — подпись запроса AWS Signature V4.

Бакет `rhl-clips` публичный на чтение: клип и обложку мини-апп берёт прямой ссылкой. Пишет только служба clips:
положить файл (PUT) и убрать (DELETE). Ключи — в /etc/rhl/bot.env, не в git:

    CLIPS_S3_KEY=…            # access key
    CLIPS_S3_SECRET=…         # secret key
    CLIPS_S3_ENDPOINT=https://s3.twcstorage.ru
    CLIPS_S3_BUCKET=rhl-clips
    CLIPS_S3_REGION=ru-1
    CLIPS_PUBLIC_URL=…        # необязательно: откуда файлы видны болельщику, по умолчанию <endpoint>/<bucket>
"""
import hashlib
import hmac
import os
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

EMPTY = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, headers: dict[str, str], payload_hash: str, key: str, secret: str,
         region: str, now: datetime, query: str = "") -> str:
    """Заголовок Authorization AWS Signature V4 (служба s3). headers — что подписываем, кроме host, x-amz-date и
    x-amz-content-sha256: их добавляем сами. path — уже в виде для URL."""
    amz = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = amz[:8]
    signed = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz,
              **{k.lower(): v.strip() for k, v in headers.items()}}
    names = sorted(signed)
    canonical = "\n".join([method, path, query, *[f"{k}:{signed[k]}" for k in names], "", ";".join(names), payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}".encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"


class Store:
    """Бакет клипов. Нет ключей — `ok` ложно: служба режет только для себя и ничего не выкладывает."""

    def __init__(self, env=os.environ):
        self.key = env.get("CLIPS_S3_KEY", "").strip()
        self.secret = env.get("CLIPS_S3_SECRET", "").strip()
        self.endpoint = (env.get("CLIPS_S3_ENDPOINT") or "https://s3.twcstorage.ru").rstrip("/")
        self.bucket = (env.get("CLIPS_S3_BUCKET") or "rhl-clips").strip()
        self.region = (env.get("CLIPS_S3_REGION") or "ru-1").strip()
        self.public = (env.get("CLIPS_PUBLIC_URL") or f"{self.endpoint}/{self.bucket}").rstrip("/")
        self.host = self.endpoint.split("://", 1)[-1]

    @property
    def ok(self) -> bool:
        return bool(self.key and self.secret and self.endpoint.startswith("https://"))

    def url(self, name: str) -> str:
        """Где файл увидит болельщик."""
        return f"{self.public}/{quote(name)}"

    def _request(self, method: str, name: str, body: bytes = b"", headers: dict[str, str] | None = None,
                 now: datetime | None = None) -> int:
        path = f"/{self.bucket}/{quote(name)}"
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY
        headers = dict(headers or {})
        now = now or datetime.now(timezone.utc)
        auth = sign(method, self.host, path, headers, payload, self.key, self.secret, self.region, now)
        req = urllib.request.Request(self.endpoint + path, data=body if method == "PUT" else None, method=method,
                                     headers={**headers, "Authorization": auth, "x-amz-content-sha256": payload,
                                              "x-amz-date": now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")})
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status

    def put(self, name: str, body: bytes, content_type: str) -> str:
        """Положить файл. Файлы не меняются (в имени — секунда гола): кэш у болельщика вечный. Возвращает адрес."""
        self._request("PUT", name, body, {"Content-Type": content_type,
                                          "Cache-Control": "public, max-age=31536000, immutable"})
        return self.url(name)

    def delete(self, name: str) -> None:
        self._request("DELETE", name)
