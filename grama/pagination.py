from rest_framework.pagination import PageNumberPagination as BasePageNumberPagination


class PageNumberPagination(BasePageNumberPagination):
    """Admin lists: ?page=1&page_size=20, at most 100 per page."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
