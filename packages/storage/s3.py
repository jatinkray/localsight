"""S3-compatible storage (optional). Imported lazily so the package has no
hard boto3 dependency for local/SQLite deployments.
"""
from __future__ import annotations

from packages.storage.base import StorageProvider


class S3CompatibleStorage(StorageProvider):
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        prefix: str,
        signing_secret: str,
    ) -> None:
        try:
            import boto3  # noqa: F401 - lazy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "boto3 is required for S3CompatibleStorage; install it or use STORAGE_BACKEND=local"
            ) from exc
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region
        )
        self._secret = signing_secret
        from packages.storage.local import LocalFilesystemStorage  # reuse signing

        self._signer = LocalFilesystemStorage("/tmp", signing_secret)

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._full_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
            return obj["Body"].read()
        except ClientError as exc:  # pragma: no cover
            raise FileNotFoundError(key) from exc

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._full_key(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=self._full_key(key))
            return True
        except ClientError:  # pragma: no cover
            return False

    def sign_get_url(self, key: str, expires_sec: int = 300) -> str:
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._full_key(key)},
            ExpiresIn=expires_sec,
        )
        return url
