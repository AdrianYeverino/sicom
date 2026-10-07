from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]

# Uploaded photos are served by Django only in development (static() does
# nothing with DEBUG off).
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
