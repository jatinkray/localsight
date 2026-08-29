"""Storage abstraction. The database stores references (storage keys), never
blobs. Initial deployment is local disk; S3-compatible object storage is an
opt-in alternative behind the same interface."""
