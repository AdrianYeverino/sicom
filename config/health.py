import logging

from django.db import Error, connection
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def database_available():
    """Runs a minimal query. An open connection is not enough: it may be dead."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Error:
        logger.exception("health: the database is not responding")
        return False
    return True


class HealthMiddleware:
    """Answers /health/ before any other layer of the app.

    Railway calls this path on every deploy to decide whether to publish the
    new version. It does so over plain HTTP, from its internal network and
    with the host name healthcheck.railway.app. Down the normal path, the
    HTTPS redirect would answer 301 and ALLOWED_HOSTS would answer 400, and
    the deploy would fail with everything fine. That is why this middleware
    goes first and ignores the host: the rest of the site keeps both checks.
    """

    PATH = "/health/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == self.PATH and request.method in ("GET", "HEAD"):
            if database_available():
                return HttpResponse("ok", content_type="text/plain; charset=utf-8")
            return HttpResponse("no database", status=503, content_type="text/plain; charset=utf-8")
        return self.get_response(request)
