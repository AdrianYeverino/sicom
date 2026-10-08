from django.urls import path

from . import views

app_name = "receiving"

urlpatterns = [
    path("", views.documents, name="documents"),
    path("documents/new/", views.document_new, name="document_new"),
    path("documents/<uuid:pk>/upload/", views.upload, name="upload"),
    path("documents/<uuid:pk>/pages/", views.page_add, name="page_add"),
    path("documents/<uuid:pk>/finish/", views.document_finish, name="finish"),
    path("documents/<uuid:pk>/", views.review, name="review"),
    path("documents/<uuid:pk>/summary/", views.document_summary, name="summary"),
    path("documents/<uuid:pk>/supplier/", views.document_supplier, name="supplier"),
    path("documents/<uuid:pk>/new-products/", views.document_new_products, name="new_products"),
    path("documents/<uuid:pk>/confirm/", views.document_confirm, name="confirm"),
    path("documents/<uuid:pk>/discard/", views.document_discard, name="discard"),
    path("pages/<uuid:page_pk>/replace/", views.page_replace, name="page_replace"),
    path("pages/<uuid:page_pk>/thumbnail.webp", views.page_thumbnail, name="thumbnail"),
    path("lines/<uuid:line_pk>/accept/", views.line_accept, name="line_accept"),
    path("lines/<uuid:line_pk>/correct/", views.line_correct, name="line_correct"),
    path("lines/<uuid:line_pk>/not-arrived/", views.line_not_arrived, name="line_not_arrived"),
    path("lines/<uuid:line_pk>/undo/", views.line_undo, name="line_undo"),
    path("lines/<uuid:line_pk>/received/", views.line_received, name="line_received"),
    path("lines/<uuid:line_pk>/price/", views.line_price, name="line_price"),
    path("lines/<uuid:line_pk>/link/", views.line_link, name="line_link"),
    path("lines/<uuid:line_pk>/new-product/", views.line_new_product, name="line_new_product"),
    path("lines/<uuid:line_pk>/products/", views.product_search, name="product_search"),
    path("entries/", views.entries, name="entries"),
    path("metrics/", views.metrics, name="metrics"),
    path("entries/<uuid:entry_pk>/", views.entry, name="entry"),
]
