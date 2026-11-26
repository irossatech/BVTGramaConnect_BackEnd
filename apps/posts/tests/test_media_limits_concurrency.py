"""A post's media limits hold when two uploads arrive at the same moment (audit BE-DB-014, BVTGC-109).

The rule (contract section 5, lead's decision of 2026-11-13): up to MAX_PHOTOS_PER_POST (3) photos, or
one video, never both. Each upload checks it before processing and again inside the save's transaction,
after its row is written, with the post row locked. Here the early check is let through to stand for
the other upload that passed it at the same moment: the second check must refuse, and the upload's row
and files must be undone.
"""

import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.posts import media
from apps.posts.models import MediaKind, PostMedia
from grama.exceptions import ApiError

pytestmark = pytest.mark.django_db

AUTHOR = "pm_kolakaluru"


def jpeg() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


def mp4() -> SimpleUploadedFile:
    return SimpleUploadedFile("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2036, content_type="video/mp4")


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """A laptop without ffmpeg: the phone sends the length and a poster (contract section 5)."""
    monkeypatch.setattr(media, "probe_duration", lambda path: None)
    monkeypatch.setattr(media, "extract_poster", lambda path, at_seconds: None)
    monkeypatch.setattr(media, "strip_metadata", lambda path, container: None)


def early_check_passes():
    """The check before processing passes, as it did for an upload that arrived at the same moment."""
    real = media._check_room

    def check(post, kind, *, written=False):
        if written:
            return real(post, kind, written=True)
        return None

    return mock.patch.object(media, "_check_room", side_effect=check)


def files_on_disk(media_root):
    return sorted(path.name for path in media_root.rglob("*") if path.is_file())


def test_a_fourth_photo_that_slipped_past_the_early_check_is_refused_and_undone(world, make_post, media_root, settings):
    assert settings.MAX_PHOTOS_PER_POST == 3
    user, post = world.users[AUTHOR], make_post(AUTHOR, "kolakaluru", submit=False)
    for _ in range(3):
        media.add_photo(user, post, jpeg())
    before = files_on_disk(media_root)
    with early_check_passes(), pytest.raises(ApiError) as refused:
        media.add_photo(user, post, jpeg())
    assert (refused.value.status_code, refused.value.error_code) == (400, "validation_error") and "file" in refused.value.fields
    assert post.media.count() == 3
    assert files_on_disk(media_root) == before  # the fourth photo's three files are gone


def test_a_photo_for_a_post_submitted_while_it_was_processed_is_refused(world, make_post, media_root):
    # BE-QUA-201: the status is checked before processing; the post may be submitted while the photo is
    # re-encoded, and media are added only to a draft or a post in recheck.
    from apps.posts.models import Post, PostStatus

    user, post = world.users[AUTHOR], make_post(AUTHOR, "kolakaluru", submit=False)
    real = media.process_image

    def processed_while_submitted(upload):
        result = real(upload)
        Post.objects.filter(pk=post.pk).update(status=PostStatus.PENDING)  # submitted from another phone
        return result

    with mock.patch.object(media, "process_image", side_effect=processed_while_submitted), \
            pytest.raises(ApiError) as refused:
        media.add_photo(user, post, jpeg())
    assert refused.value.error_code == "invalid_transition"
    assert not PostMedia.objects.filter(post_id=post.pk).exists()
    assert files_on_disk(media_root) == []


def test_a_photo_next_to_a_video_that_arrived_at_the_same_moment_is_refused(world, make_post, media_root, no_ffmpeg):
    user, post = world.users[AUTHOR], make_post(AUTHOR, "kolakaluru", submit=False)
    media.add_video(user, post, mp4(), duration_s=30, poster=jpeg())
    before = files_on_disk(media_root)
    with early_check_passes(), pytest.raises(ApiError) as refused:
        media.add_photo(user, post, jpeg())
    assert (refused.value.status_code, refused.value.error_code) == (409, "media_conflict")
    assert list(post.media.values_list("kind", flat=True)) == [MediaKind.VIDEO]
    assert files_on_disk(media_root) == before


def test_a_video_next_to_a_photo_that_arrived_at_the_same_moment_is_refused(world, make_post, media_root, no_ffmpeg):
    user, post = world.users[AUTHOR], make_post(AUTHOR, "kolakaluru", submit=False)
    media.add_photo(user, post, jpeg())
    before = files_on_disk(media_root)
    with early_check_passes(), pytest.raises(ApiError) as refused:
        media.add_video(user, post, mp4(), duration_s=30, poster=jpeg())
    assert (refused.value.status_code, refused.value.error_code) == (409, "media_conflict")
    assert list(post.media.values_list("kind", flat=True)) == [MediaKind.IMAGE]
    assert files_on_disk(media_root) == before


def test_a_second_video_that_arrived_at_the_same_moment_is_refused(world, make_post, media_root, no_ffmpeg):
    user, post = world.users[AUTHOR], make_post(AUTHOR, "kolakaluru", submit=False)
    media.add_video(user, post, mp4(), duration_s=30, poster=jpeg())
    with early_check_passes(), pytest.raises(ApiError) as refused:
        media.add_video(user, post, mp4(), duration_s=20, poster=jpeg())
    assert refused.value.error_code == "media_conflict" and PostMedia.objects.filter(post=post).count() == 1


def test_within_the_limits_every_upload_still_works(world, make_post, media_root, no_ffmpeg):
    user = world.users[AUTHOR]
    photos = make_post(AUTHOR, "kolakaluru", submit=False)
    for order in range(3):
        assert media.add_photo(user, photos, jpeg()).order == order
    video = make_post(AUTHOR, "kolakaluru", submit=False, title="వీడియో")
    assert media.add_video(user, video, mp4(), duration_s=30, poster=jpeg()).kind == MediaKind.VIDEO
