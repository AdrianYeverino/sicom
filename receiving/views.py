from django.shortcuts import render

from .models import ReceivedDocument


def documents(request):
    return render(request, "receiving/documents.html", {"documents": ReceivedDocument.objects.select_related("supplier")[:50]})
