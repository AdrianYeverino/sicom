from unittest import mock

from django.test import TestCase, override_settings


class HealthTest(TestCase):
    """The path Railway uses to decide whether to publish a new version."""

    def test_ok_with_the_database_available(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_accepts_head(self):
        self.assertEqual(self.client.head("/health/").status_code, 200)

    @override_settings(ALLOWED_HOSTS=["sicom.example"], SECURE_SSL_REDIRECT=True)
    def test_answers_over_http_with_the_railway_host_name(self):
        response = self.client.get("/health/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["sicom.example"], SECURE_SSL_REDIRECT=True)
    def test_the_rest_of_the_site_still_rejects_that_host(self):
        with self.assertLogs("django.security.DisallowedHost", level="ERROR"):
            response = self.client.get("/admin/login/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 400)

    def test_503_without_database(self):
        with mock.patch("config.health.database_available", return_value=False):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
