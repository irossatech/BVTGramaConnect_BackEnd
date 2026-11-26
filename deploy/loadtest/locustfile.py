"""Load test: the realistic mix of the app and the console against a STAGING server.

Run it through the runner, which checks the config, refuses production and qa, steps the user numbers
and writes the reports (deploy/loadtest/profiles.json, .env.test):

    python -m grama.perftest load      # 1, 5, 10, 25, 50 users in turn
    python -m grama.perftest stress    # more users each round until it breaks
    python -m grama.perftest spike     # 0 to 50 users at once, and the recovery time
    python -m grama.perftest soak      # 10 minutes sustained

The mix, by user class (weights):
- Citizen (20): picks a village like the app's picker, opens the feed, scrolls, opens posts, the Contacts tab.
- Official (2, only with LOADTEST_OFFICIAL_USERNAME and _PASSWORD set): logs in, refreshes the token,
  reads notifications and the post list, writes a draft post and deletes it again (nothing is published).
- FailedLogin (1): wrong passwords for made-up usernames. They get 400 invalid_credentials and, once a
  pair is locked, 429 locked_out; both count as expected. A username also locks from every address after
  LOGIN_MAX_FAILURES_PER_USERNAME (20) failures in an hour.

Tags select parts of the mix: feed, post, contacts, login, refresh, notifications, create.
No host, user or password is written here: they come from the environment the runner passes.
"""

import os
import random

from locust import HttpUser, between, tag, task
from locust.exception import StopUser

API = "/api/v1"


class Citizen(HttpUser):
    """Opens the feed for their village, scrolls, opens posts."""

    weight = 20
    wait_time = between(2, 8)

    def _ids(self, path, name):
        response = self.client.get(f"{API}{path}", name=name)
        return [row["id"] for row in response.json()] if response.ok else []

    def on_start(self):
        """Pick a village the way the app's picker does: state, district, mandal, village."""
        self.village_ids = []
        states = self._ids("/geo/states/", "geo states")
        districts = self._ids(f"/geo/districts/?state={random.choice(states)}", "geo districts") if states else []
        mandals = self._ids(f"/geo/mandals/?district={random.choice(districts)}", "geo mandals") if districts else []
        if mandals:
            self.village_ids = self._ids(f"/geo/villages/?mandal={random.choice(mandals)}", "geo villages")
        if not self.village_ids:
            self.village_ids = [1]
        self.village_id = random.choice(self.village_ids)
        self.etag = None

    # Weights are x10 so the Contacts tab can sit at one tenth of the feed (it is opened far less).
    @tag("feed")
    @task(60)
    def open_feed(self):
        headers = {"If-None-Match": self.etag} if self.etag else {}
        with self.client.get(
            f"{API}/feed/?village_id={self.village_id}",
            headers=headers,
            name="feed first page",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 304):
                self.etag = response.headers.get("ETag", self.etag)
                response.success()
                if response.status_code == 200:
                    self.posts = response.json().get("results", [])
                    self.next_url = response.json().get("next")
            else:
                response.failure(f"status {response.status_code}")

    @tag("feed")
    @task(30)
    def scroll(self):
        next_url = getattr(self, "next_url", None)
        if next_url:
            response = self.client.get(next_url, name="feed next page")
            if response.ok:
                self.next_url = response.json().get("next")

    @tag("post")
    @task(20)
    def open_post(self):
        posts = getattr(self, "posts", [])
        if posts:
            post = random.choice(posts)
            self.client.get(f"{API}/feed/posts/{post['id']}/", name="feed post detail")

    @tag("contacts")
    @task(6)
    def open_contacts(self):
        """The Contacts tab: one call, cached with an ETag like the feed."""
        etag = getattr(self, "contacts_etag", None)
        with self.client.get(
            f"{API}/contacts/?village_id={self.village_id}",
            headers={"If-None-Match": etag} if etag else {},
            name="contacts",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 304):
                self.contacts_etag = response.headers.get("ETag", etag)
                response.success()
            else:
                response.failure(f"status {response.status_code}")

    @tag("feed")
    @task(10)
    def change_village(self):
        self.village_id = random.choice(self.village_ids)
        self.etag = None
        self.contacts_etag = None


class Official(HttpUser):
    """A staging official: login, token refresh, notifications, the post list, a draft written and deleted."""

    weight = 2
    wait_time = between(3, 10)

    def on_start(self):
        username = os.environ.get("LOADTEST_OFFICIAL_USERNAME", "")
        password = os.environ.get("LOADTEST_OFFICIAL_PASSWORD", "")
        if not username or not password:
            raise StopUser()
        response = self.client.post(f"{API}/auth/login/", json={"username": username, "password": password},
                                    name="official login")
        if not response.ok:
            raise StopUser()
        tokens = response.json()
        self.access, self.refresh = tokens["access"], tokens["refresh"]
        self.target = None

    def headers(self):
        return {"Authorization": f"Bearer {self.access}"}

    @tag("refresh")
    @task(2)
    def renew(self):
        response = self.client.post(f"{API}/auth/refresh/", json={"refresh": self.refresh}, name="token refresh")
        if response.ok:
            self.access = response.json()["access"]
            self.refresh = response.json().get("refresh", self.refresh)

    @tag("notifications")
    @task(10)
    def notifications(self):
        self.client.get(f"{API}/notifications/", headers=self.headers(), name="notifications")

    @tag("post")
    @task(8)
    def post_list(self):
        self.client.get(f"{API}/posts/", headers=self.headers(), name="official post list")

    @tag("create")
    @task(2)
    def create_draft(self):
        if self.target is None:
            targets = self.client.get(f"{API}/posts/targets/", headers=self.headers(), name="post targets")
            options = targets.json() if targets.ok else []
            options = options.get("results", options) if isinstance(options, dict) else options
            if not options:
                return
            self.target = {"level": options[0]["level"], "id": options[0]["id"]}
        body = {"category": "news", "language": "te", "title": "లోడ్ పరీక్ష", "body": "పరీక్ష మాత్రమే.",
                "targets": [self.target], "submit": False}
        created = self.client.post(f"{API}/posts/", json=body, headers=self.headers(), name="create draft post")
        if created.status_code == 201:
            self.client.delete(f"{API}/posts/{created.json()['id']}/", headers=self.headers(), name="delete draft post")


class FailedLogin(HttpUser):
    """Tries to log in. Unknown usernames get the same answer as a wrong password."""

    weight = 1
    wait_time = between(5, 15)

    @tag("login")
    @task
    def log_in(self):
        username = f"loadtest{random.randint(0, 999_999)}"
        with self.client.post(
            f"{API}/auth/login/",
            json={"username": username, "password": "not-the-password"},
            name="failed login",
            catch_response=True,
        ) as response:
            if response.status_code in (400, 429):
                response.success()
            else:
                response.failure(f"status {response.status_code}")
