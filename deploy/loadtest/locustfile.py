"""Load test for the citizen feed and the login endpoint.

Run against a server that has sample data (python manage.py seed_sample_data):

    locust -f deploy/loadtest/locustfile.py --host http://127.0.0.1:8000 \
        --users [EXPECTED USERS] --spawn-rate 20 --run-time 5m --headless --csv deploy/loadtest/results/run

Replace [EXPECTED USERS] with the expected number of people using the app at
the same time. Citizens far outnumber officials, so the user classes are
weighted 20:1.

Failed logins lock a username + IP pair after LOGIN_MAX_FAILURES (5) tries
for LOGIN_LOCKOUT_MINUTES (django-axes), on purpose. The login users below
guess wrong passwords for made-up usernames, so they get 400
invalid_credentials and, once a pair is locked, 429 locked_out; both count as
expected, not as failures. To measure the login path itself, raise
LOGIN_MAX_FAILURES on the test server only.
"""

import random

from locust import HttpUser, between, task

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

    # Weights are ×10 so the Contacts tab can sit at one tenth of the feed (it is opened far less).
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

    @task(30)
    def scroll(self):
        next_url = getattr(self, "next_url", None)
        if next_url:
            response = self.client.get(next_url, name="feed next page")
            if response.ok:
                self.next_url = response.json().get("next")

    @task(20)
    def open_post(self):
        posts = getattr(self, "posts", [])
        if posts:
            post = random.choice(posts)
            self.client.get(f"{API}/feed/posts/{post['id']}/", name="feed post detail")

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

    @task(10)
    def change_village(self):
        self.village_id = random.choice(self.village_ids)
        self.etag = None
        self.contacts_etag = None


class OfficialLogin(HttpUser):
    """Tries to log in. Unknown usernames get the same answer as a wrong password."""

    weight = 1
    wait_time = between(5, 15)

    @task
    def log_in(self):
        username = f"loadtest{random.randint(0, 999_999)}"
        with self.client.post(
            f"{API}/auth/login/",
            json={"username": username, "password": "not-the-password"},
            name="login",
            catch_response=True,
        ) as response:
            if response.status_code in (400, 429):
                response.success()
            else:
                response.failure(f"status {response.status_code}")
