"""
s3_loader.py — загрузка документов из Yandex Cloud Object Storage

Предоставляет:
  list_documents()   — список файлов с ETag (без скачивания)
  download_file()    — скачать конкретный файл по ключу

ETag в S3 = md5 содержимого файла, вычисляется автоматически.
Сравнение по ETag позволяет обнаружить изменения без скачивания файла.

Зависимости:
    pip install boto3
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import (
    YC_KEY_ID,
    YC_SECRET_KEY,
    YC_BUCKET,
    YC_PREFIX,
    YC_ENDPOINT,
    YC_REGION,
    SUPPORTED_DOC_EXTENSIONS,
)

logger = logging.getLogger("s3_loader")


# ─────────────────────────────────────────────────────────────────────────────
#  S3-клиент
# ─────────────────────────────────────────────────────────────────────────────

def _make_client():
    """Создаёт boto3-клиент для Yandex Cloud Object Storage."""
    return boto3.client(
        "s3",
        endpoint_url=YC_ENDPOINT,
        region_name=YC_REGION,
        aws_access_key_id=YC_KEY_ID,
        aws_secret_access_key=YC_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Листинг бакета
# ─────────────────────────────────────────────────────────────────────────────

def list_documents(
    bucket: str = YC_BUCKET,
    prefix: str = YC_PREFIX,
) -> dict[str, str]:
    """
    Возвращает словарь: s3_key → etag для всех поддерживаемых файлов в бакете.

    ETag возвращается S3 при листинге — скачивать файлы не нужно.
    По изменению ETag определяем что файл обновился.

    Args:
        bucket: имя бакета
        prefix: папка внутри бакета (пустая строка = весь бакет)

    Returns:
        { "path/to/file.pdf": "d41d8cd98f00b204e9800998ecf8427e", ... }
    """
    s3     = _make_client()
    result: dict[str, str] = {}

    try:
        paginator = s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                # Пропускаем файлы неподдерживаемых форматов
                if Path(key).suffix.lower() not in SUPPORTED_DOC_EXTENSIONS:
                    continue

                # ETag приходит в двойных кавычках → убираем
                etag = obj.get("ETag", "").strip('"')
                result[key] = etag

        logger.info(
            f"[s3] Бакет '{bucket}': найдено {len(result)} документов "
            f"({', '.join(SUPPORTED_DOC_EXTENSIONS)})"
        )

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDenied":
            logger.error(
                f"[s3] Нет доступа к бакету '{bucket}'. "
                "Проверьте YC_KEY_ID, YC_SECRET_KEY и права сервисного аккаунта."
            )
        elif code == "NoSuchBucket":
            logger.error(f"[s3] Бакет '{bucket}' не существует")
        else:
            logger.error(f"[s3] Ошибка S3 ({code}): {e}")

    except BotoCoreError as e:
        logger.error(f"[s3] Сетевая ошибка при листинге: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Скачивание файла
# ─────────────────────────────────────────────────────────────────────────────

def download_file(
    key:    str,
    bucket: str = YC_BUCKET,
) -> Optional[bytes]:
    """
    Скачивает файл из S3 и возвращает байты.

    Args:
        key:    S3-ключ файла (путь внутри бакета)
        bucket: имя бакета

    Returns:
        Байты файла или None при ошибке.
    """
    s3 = _make_client()

    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        data = resp["Body"].read()
        size_kb = len(data) // 1024
        logger.info(f"[s3] Скачан: {key} ({size_kb} КБ)")
        return data

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchKey":
            logger.error(f"[s3] Файл не найден: {key}")
        elif code == "AccessDenied":
            logger.error(f"[s3] Нет доступа к файлу: {key}")
        else:
            logger.error(f"[s3] Ошибка скачивания {key} ({code}): {e}")

    except BotoCoreError as e:
        logger.error(f"[s3] Сетевая ошибка при скачивании {key}: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Публичный URL документа
# ─────────────────────────────────────────────────────────────────────────────

def public_url(key: str, bucket: str = YC_BUCKET) -> str:
    """Возвращает публичный URL файла в Yandex Cloud Storage."""
    return f"{YC_ENDPOINT}/{bucket}/{key}"
