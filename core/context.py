from django.conf import settings


def store(request):
    return {"store_name": settings.STORE_NAME}
