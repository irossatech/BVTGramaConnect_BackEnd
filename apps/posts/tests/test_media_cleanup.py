"""No media file is left on disk without its row (audit finding BE-DB-015).

- A photo, a video or a citizen post whose save fails leaves no files behind (its rows are rolled back).
- Deleting a draft removes every file of its media, the video included.
"""

import io
import uuid
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from PIL import Image

from apps.citizens import services as citizen_services
from apps.citizens.models import CitizenDevice
from apps.posts import media, services
from apps.posts.models import Category, MediaKind, Post, PostMedia

pytestmark = pytest.mark.django_db


def jpeg() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


def files_on_disk(media_root):
    return sorted(path.name for path in media_root.rglob("*") if path.is_file())


def test_a_saved_photo_keeps_its_three_files(world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media.add_photo(world.users["pm_kolakaluru"], post, jpeg())
    assert len(files_on_disk(media_root)) == 3  # 1280, 640 and 320 px


def test_a_photo_whose_row_fails_leaves_no_file(world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    with mock.patch.object(PostMedia, "save", side_effect=IntegrityError("boom")):
        with pytest.raises(IntegrityError):
            media.add_photo(world.users["pm_kolakaluru"], post, jpeg())
    assert files_on_disk(media_root) == []
    assert not PostMedia.objects.exists()


def test_a_citizen_post_that_fails_after_its_photos_leaves_no_file(world, media_root):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n-1",
    )
    with mock.patch.object(services, "_log", side_effect=RuntimeError("boom")):  # after the photos are stored
        with pytest.raises(RuntimeError):
            citizen_services.create_post(
                device, category=Category.objects.get(key="news"), language="te", title="పైపు", body="b",
                photos=[jpeg(), jpeg()],
            )
    assert files_on_disk(media_root) == []
    assert not Post.objects.exists() and not PostMedia.objects.exists()


def test_deleting_a_draft_removes_its_video_file_too(world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    names = {
        field: default_storage.save(f"posts/2026/11/clip-{field}.bin", ContentFile(b"x"))
        for field in ("video", "image", "medium", "thumbnail")
    }
    PostMedia.objects.create(post=post, kind=MediaKind.VIDEO, width=1280, height=720, bytes=1, **names)
    assert len(files_on_disk(media_root)) == 4
    services.delete_post(world.users["pm_kolakaluru"], post)
    assert files_on_disk(media_root) == []
