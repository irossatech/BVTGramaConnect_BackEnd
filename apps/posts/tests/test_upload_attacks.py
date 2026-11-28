"""Hostile photo uploads (audit stage S6, BVTGC-17, security category).

A post photo is opened by Pillow and stored only as new WebP files (apps/posts/media.py), so:
- a "decompression bomb" (a small file that claims huge dimensions) is refused before it is decoded;
- a polyglot (a real JPEG with an HTML page or a ZIP archive appended) is stored without the extra part;
- a file's name and Content-Type do not matter, only its content: a PNG sent as "photo.jpg" is fine,
  an HTML page or an SVG with a script sent as an image is refused;
- a refused upload leaves nothing behind.
EXIF and GPS removal is in test_media_escalation.py, the size limit in test_media_limits*.py.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.posts.models import PostMedia

pytestmark = pytest.mark.django_db


def upload(api, world, post, file, actor="pm_kolakaluru"):
    return api(world.users[actor]).post(f"/api/v1/posts/{post.id}/media/", {"file": file}, format="multipart")


def jpeg_bytes(size=(800, 600)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (31, 138, 59)).save(buffer, "JPEG")
    return buffer.getvalue()


def png_claiming(width: int, height: int) -> bytes:
    """A tiny PNG whose header claims width x height pixels (a decompression bomb's header)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


def stored_files(post) -> list[bytes]:
    out = []
    for media in PostMedia.objects.filter(post=post):
        for field in (media.image, media.medium, media.thumbnail):
            if field:
                with field.open("rb") as handle:
                    out.append(handle.read())
    return out


@pytest.mark.parametrize("width, height", [(50_000, 50_000), (100_000, 1_000), (8_000, 6_000)])
def test_a_picture_claiming_huge_dimensions_is_refused_before_decoding(api, world, make_post, width, height):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, post, SimpleUploadedFile("bomb.png", png_claiming(width, height), content_type="image/png"))
    assert response.status_code == 415, response.content  # contract section 5: not a photo, 415 unsupported_media
    assert response.json()["error"]["code"] == "unsupported_media"
    assert not PostMedia.objects.filter(post=post).exists()


@pytest.mark.parametrize("tail", [
    b"<html><body><script>alert(document.cookie)</script></body></html>",
    b"PK\x03\x04" + b"\x14\x00" * 10 + b"evil.sh#!/bin/sh\nrm -rf /\n",
    b"<?php system($_GET['c']); ?>",
])
def test_a_polyglot_is_stored_without_its_hidden_part(api, world, make_post, tail):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, post, SimpleUploadedFile("photo.jpg", jpeg_bytes() + tail, content_type="image/jpeg"))
    assert response.status_code == 201, response.content
    files = stored_files(post)
    assert len(files) == 3
    for data in files:
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert tail[:12] not in data and b"<script" not in data and b"<?php" not in data


def test_the_name_and_content_type_do_not_decide_a_png_sent_as_jpeg_is_fine(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 10, 10)).save(buffer, "PNG")
    response = upload(api, world, post, SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg"))
    assert response.status_code == 201, response.content
    assert all(data[8:12] == b"WEBP" for data in stored_files(post))


@pytest.mark.parametrize("name, content_type, content", [
    ("photo.jpg", "image/jpeg", b"<html><script>alert(1)</script></html>"),
    ("photo.svg", "image/svg+xml",
     b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script></svg>'),
    ("photo.gif", "image/gif", b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                               b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"),
    ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 200),  # a JPEG start with nothing behind it
])
def test_what_is_not_a_jpeg_png_or_webp_is_refused(api, world, make_post, name, content_type, content):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, post, SimpleUploadedFile(name, content, content_type=content_type))
    assert response.status_code == 415, response.content
    assert response.json()["error"]["code"] == "unsupported_media"
    assert not PostMedia.objects.filter(post=post).exists()


def test_an_empty_file_is_refused(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, post, SimpleUploadedFile("photo.jpg", b"", content_type="image/jpeg"))
    assert response.status_code in (400, 415), response.content
    assert "error" in response.json()
    assert not PostMedia.objects.filter(post=post).exists()


def test_the_photo_formats_in_the_contract_are_the_ones_the_server_takes():
    """docs/api-contract.md, section 5, "Images: …" names exactly what apps/posts/media.py takes (MPO is a
    JPEG variant). It once promised HEIC, which the server refuses (finding BE-TEST-209, contract corrected)."""
    import re
    from pathlib import Path

    from django.conf import settings

    from apps.posts.media import ALLOWED_FORMATS

    contract = (Path(settings.BASE_DIR) / "docs" / "api-contract.md").read_text(encoding="utf-8")
    line = re.search(r"^- Images: (.+?) or (\w+)[ ,(]", contract, re.M)
    promised = {name.strip().upper() for name in line.group(1).split(",")} | {line.group(2).upper()}
    assert promised <= ALLOWED_FORMATS | {"JPG"}, f"promised {sorted(promised)}, taken {sorted(ALLOWED_FORMATS)}"
