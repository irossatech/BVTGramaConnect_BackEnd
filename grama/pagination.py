from functools import partial

from django.core.paginator import Paginator
from rest_framework.pagination import PageNumberPagination as BasePageNumberPagination


class PageNumberPagination(BasePageNumberPagination):
    """Admin lists: ?page=1&page_size=20, at most 100 per page."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class KnownCountPaginator(Paginator):
    """A Django paginator told its row count, so it never runs a COUNT(*) of its own."""

    def __init__(self, *args, count: int, **kwargs):
        super().__init__(*args, **kwargs)
        self._known_count = count

    @property
    def count(self) -> int:
        return self._known_count


def use_known_count(pagination, total: int) -> None:
    """For a list that already counted its rows (its tab counts): page with that total, no second count."""
    pagination.django_paginator_class = partial(KnownCountPaginator, count=total)
