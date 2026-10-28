"""Machine translation of published posts, Telugu <-> English (contract section 6)."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_q.models import Schedule
from rest_framework.test import APIClient

from apps.citizens.tests.conftest import phone  # noqa: F401  (fixture: registers a citizen phone)
from apps.posts import translation
from apps.posts.models import Post, PostStatus, PostTranslation
from apps.posts.services import PostInput, create_post, review, submit_post, update_post
from apps.posts.tasks import translate_post
from apps.posts.translation import LocmemTranslator, detect_language, translate_post_text

pytestmark = pytest.mark.django_db

TELUGU_TITLE, TELUGU_BODY = "గ్రామ సభ సమావేశం", "అక్టోబర్ 20న గ్రామ సభ.\n\nపంచాయతీ కార్యాలయంలో."
LOCMEM = "apps.posts.translation.LocmemTranslator"


@pytest.fixture
def provider(settings):
    settings.TRANSLATION_PROVIDER = LOCMEM
    LocmemTranslator.calls.clear()
    LocmemTranslator.failures.clear()
    yield LocmemTranslator
    LocmemTranslator.failures.clear()


@pytest.fixture
def publish(world, announce, django_capture_on_commit_callbacks):
    """publish(author, target, title, body): a post its author publishes at once (an admin), translated on commit."""

    def make(author="va_kolakaluru", target="kolakaluru", title=TELUGU_TITLE, body=TELUGU_BODY, language="te",
             submit=True):
        with django_capture_on_commit_callbacks(execute=True):
            return create_post(world.users[author], PostInput(category=announce, language=language, title=title,
                                                              body=body, target=world.ref(target)), submit=submit)

    return make


def feed_row(world, post):
    rows = APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}").json()["results"]
    return next((row for row in rows if row["id"] == str(post.pk)), None)


def test_a_published_telugu_post_gets_an_english_translation(world, provider, publish):
    post = publish()
    assert post.status == PostStatus.PUBLISHED
    stored = PostTranslation.objects.get(post=post)
    assert (stored.language, stored.provider, stored.source_hash) == ("en", "locmem", post.source_hash)
    assert stored.title == f"[en] {TELUGU_TITLE}"
    # The body goes line by line, so paragraphs and empty lines survive.
    assert stored.body == "[en] అక్టోబర్ 20న గ్రామ సభ.\n\n[en] పంచాయతీ కార్యాలయంలో."
    assert provider.calls == [([TELUGU_TITLE, "అక్టోబర్ 20న గ్రామ సభ.", "పంచాయతీ కార్యాలయంలో."], "te", "en")]

    expected = {"language": "en", "title": stored.title, "body": stored.body, "machine": True}
    assert feed_row(world, post)["translation"] == expected
    assert APIClient().get(f"/api/v1/feed/posts/{post.pk}/").json()["translation"] == expected
    official = APIClient()
    official.force_authenticate(world.users["va_kolakaluru"])
    assert official.get(f"/api/v1/posts/{post.pk}/").json()["translation"] == expected


def test_an_english_post_gets_a_telugu_translation(world, provider, publish):
    post = publish(title="Gram Sabha on 20 October", body="At the Panchayat office, 10 AM.", language="en")
    assert feed_row(world, post)["translation"]["language"] == "te"
    assert feed_row(world, post)["translation"]["title"] == "[te] Gram Sabha on 20 October"


def test_nothing_is_sent_before_a_post_is_published(world, provider, publish, django_capture_on_commit_callbacks):
    pending = publish(author="pm_kolakaluru")  # a Panchayat member's post waits for review
    draft = publish(submit=False)
    assert (pending.status, draft.status) == (PostStatus.PENDING, PostStatus.DRAFT)
    assert provider.calls == [] and not PostTranslation.objects.exists()
    assert feed_row(world, pending) is None

    with django_capture_on_commit_callbacks(execute=True):
        review(world.users["va_kolakaluru"], pending, "publish")
    assert PostTranslation.objects.filter(post=pending, language="en").exists()


def test_a_taken_down_post_keeps_its_translation_but_leaves_the_feed(world, provider, publish,
                                                                     django_capture_on_commit_callbacks):
    post = publish()
    with django_capture_on_commit_callbacks(execute=True):
        review(world.users["va_kolakaluru"], post, "unpublish", comment="Wrong date")
    assert PostTranslation.objects.filter(post=post).exists() and feed_row(world, post) is None
    # Put back by a higher level: the translation is current, so the provider is not called again.
    provider.calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        review(world.users["ma_tenali"], Post.objects.get(pk=post.pk), "reverse", comment="Date is right",
               to_status=PostStatus.PUBLISHED)
    assert provider.calls == [] and feed_row(world, post)["translation"] is not None


def test_an_edited_post_is_translated_again_when_it_is_published_again(world, provider, publish,
                                                                       django_capture_on_commit_callbacks):
    post = publish()
    with django_capture_on_commit_callbacks(execute=True):
        review(world.users["ma_tenali"], post, "reverse", comment="Add the time", to_status=PostStatus.RECHECK)
    post = update_post(world.users["va_kolakaluru"], Post.objects.get(pk=post.pk), {"body": "ఉదయం 10 గంటలకు."})
    official = APIClient()
    official.force_authenticate(world.users["va_kolakaluru"])
    # Between the edit and the new translation, the old one is not served.
    assert official.get(f"/api/v1/posts/{post.pk}/").json()["translation"] is None

    with django_capture_on_commit_callbacks(execute=True):
        submit_post(world.users["va_kolakaluru"], post)
    stored = PostTranslation.objects.get(post=post)  # updated in place: one per language
    assert stored.body == "[en] ఉదయం 10 గంటలకు." and stored.source_hash == Post.objects.get(pk=post.pk).source_hash
    assert feed_row(world, post)["translation"]["body"] == "[en] ఉదయం 10 గంటలకు."


def test_a_failing_provider_is_retried_later_and_never_blocks_publishing(world, provider, publish, settings):
    provider.failures.extend([RuntimeError("down")] * 4)
    post = publish()
    assert post.status == PostStatus.PUBLISHED and feed_row(world, post)["translation"] is None
    retry = Schedule.objects.get()
    assert (retry.func, retry.args, retry.schedule_type) == ("apps.posts.tasks.translate_post", repr((str(post.pk), 2)), "O")

    # The worker runs each one-off schedule; the waits are 1, 5 and 15 minutes.
    for attempt in (2, 3, 4):
        assert translate_post(str(post.pk), attempt) is False
    assert Schedule.objects.count() == 3  # attempts 2, 3 and 4; none after the last
    assert not PostTranslation.objects.exists() and feed_row(world, post)["translation"] is None


def test_a_retry_that_works_saves_the_translation(world, provider, publish):
    provider.failures.append(RuntimeError("down"))
    post = publish()
    assert translate_post(str(post.pk), 2) is True
    assert feed_row(world, post)["translation"]["language"] == "en"


def test_switched_off_nothing_is_sent_or_queued(world, settings, publish):
    settings.TRANSLATION_PROVIDER = "off"
    LocmemTranslator.calls.clear()
    post = publish()
    assert LocmemTranslator.calls == [] and not PostTranslation.objects.exists()
    assert feed_row(world, post)["translation"] is None
    with pytest.raises(CommandError, match="off"):
        call_command("translate_published_posts")


@pytest.mark.parametrize(
    "title, body, given, stored",
    [
        (TELUGU_TITLE, TELUGU_BODY, "en", "te"),  # the app said English, the text is Telugu
        ("Water supply", "Tomorrow 6 AM.", "te", "en"),
        ("Gram Sabha at Kolakaluru", "Meeting at కొలకలూరు panchayat office, 10 AM.", "en", "en"),  # one Telugu name
        ("123", "10:00", "te", "te"),  # no letters: what the author sent
    ],
)
def test_the_stored_language_follows_the_text(world, publish, title, body, given, stored):
    assert publish(title=title, body=body, language=given).language == stored


def test_an_edit_corrects_the_language_too(world, publish):
    post = publish(submit=False)
    edited = update_post(world.users["va_kolakaluru"], post, {"title": "Water supply", "body": "Tomorrow 6 AM."})
    assert edited.language == "en"


def test_citizens_see_the_translation_of_their_published_post(world, provider, phone,
                                                             django_capture_on_commit_callbacks):
    citizen = phone("kolakaluru")
    with django_capture_on_commit_callbacks(execute=True):
        response = citizen.post(title=TELUGU_TITLE, body=TELUGU_BODY)
    assert response.status_code == 201, response.json()
    post = Post.objects.get(pk=response.json()["id"])
    assert citizen.client.get("/api/v1/citizen/posts/").json()[0]["translation"] is None  # pending
    with django_capture_on_commit_callbacks(execute=True):
        review(world.users["va_kolakaluru"], post, "publish")
    assert citizen.client.get("/api/v1/citizen/posts/").json()[0]["translation"]["language"] == "en"


def test_a_feed_page_costs_the_same_queries_with_translations(world, provider, publish, settings):
    def queries():
        with CaptureQueriesContext(connection) as captured:
            APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}")
        return len(captured)

    for n in range(3):
        publish(title=f"{TELUGU_TITLE} {n}")
    with_three = queries()
    for n in range(3, 6):
        publish(title=f"{TELUGU_TITLE} {n}")
    assert PostTranslation.objects.count() == 6
    assert queries() == with_three


def test_the_backfill_queues_published_posts_without_one(world, settings, publish):
    settings.TRANSLATION_PROVIDER = "off"
    first, second = publish(), publish(title=f"{TELUGU_TITLE} 2")
    publish(author="pm_kolakaluru")  # pending: never
    settings.TRANSLATION_PROVIDER = LOCMEM
    LocmemTranslator.calls.clear()
    out = StringIO()
    call_command("translate_published_posts", "--since", "2026-01-01", stdout=out)
    assert "Queued 2 post(s)" in out.getvalue()
    assert set(PostTranslation.objects.values_list("post_id", flat=True)) == {first.pk, second.pk}
    out = StringIO()
    call_command("translate_published_posts", stdout=out)
    assert "Queued 0 post(s)" in out.getvalue()


# --- The providers' requests ------------------------------------------------------------------------


class Reply:
    def __init__(self, data, status=200):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_google_gets_the_texts_and_the_key_in_a_header(settings, monkeypatch):
    settings.TRANSLATION_PROVIDER, settings.GOOGLE_TRANSLATE_KEY = "google", "secret-key"
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return Reply({"data": {"translations": [{"translatedText": "Gram Sabha"}, {"translatedText": "On the 20th."}]}})

    monkeypatch.setattr(translation.requests, "post", post)
    assert translate_post_text(translation.get_translator(), "గ్రామ సభ", "20న.", "te", "en") == ("Gram Sabha", "On the 20th.")
    (url, kwargs), = sent
    assert "secret-key" not in url and kwargs["headers"] == {"X-goog-api-key": "secret-key"}
    assert kwargs["json"] == {"q": ["గ్రామ సభ", "20న."], "source": "te", "target": "en", "format": "text"}


def test_bhashini_asks_the_pipeline_once_then_translates(settings, monkeypatch):
    settings.TRANSLATION_PROVIDER = "bhashini"
    settings.BHASHINI_USER_ID, settings.BHASHINI_API_KEY, settings.BHASHINI_PIPELINE_ID = "user", "key", "pipe"
    translation.BhashiniTranslator._endpoints.clear()
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        if url == translation.BhashiniTranslator.CONFIG_URL:
            return Reply({
                "pipelineResponseConfig": [{"config": [{"serviceId": "ai4bharat/indictrans"}]}],
                "pipelineInferenceAPIEndPoint": {"callbackUrl": "https://dhruva.example/infer",
                                                 "inferenceApiKey": {"name": "Authorization", "value": "infer-key"}},
            })
        return Reply({"pipelineResponse": [{"output": [{"source": "x", "target": "Gram Sabha"}]}]})

    monkeypatch.setattr(translation.requests, "post", post)
    translator = translation.get_translator()
    assert translator.translate(["గ్రామ సభ"], "te", "en") == ["Gram Sabha"]
    assert translator.translate(["గ్రామ సభ"], "te", "en") == ["Gram Sabha"]
    assert [url for url, _kw in sent] == [translation.BhashiniTranslator.CONFIG_URL, "https://dhruva.example/infer",
                                         "https://dhruva.example/infer"]
    assert sent[0][1]["headers"] == {"userID": "user", "ulcaApiKey": "key"}
    assert sent[1][1]["headers"] == {"Authorization": "infer-key"}
    assert sent[1][1]["json"]["inputData"] == {"input": [{"source": "గ్రామ సభ"}]}
    translation.BhashiniTranslator._endpoints.clear()


def test_a_provider_error_never_carries_the_text(settings, monkeypatch):
    settings.TRANSLATION_PROVIDER, settings.GOOGLE_TRANSLATE_KEY = "google", "k"
    monkeypatch.setattr(translation.requests, "post", lambda url, **kw: Reply({"error": "గ్రామ సభ"}, status=403))
    with pytest.raises(RuntimeError) as error:
        translation.get_translator().translate(["గ్రామ సభ"], "te", "en")
    assert str(error.value) == "Google answered 403"


def test_a_provider_without_its_keys_refuses_to_start(settings):
    settings.TRANSLATION_PROVIDER, settings.GOOGLE_TRANSLATE_KEY = "google", ""
    with pytest.raises(RuntimeError, match="GOOGLE_TRANSLATE_KEY"):
        translation.get_translator()


def test_detect_language_counts_letters():
    assert detect_language("రేపు 6 AM", "en") == "te"
    assert detect_language("", "te") == "te"
