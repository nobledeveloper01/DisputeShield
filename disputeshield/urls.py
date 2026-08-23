"""Two URL namespaces, kept apart on purpose.

`/v1/widget/*` is session-token scoped and serialises customer-visible fields
only. `/v1/*` is agent scoped. They never share a serializer (§10), and the
leakage test asserts that no field path crosses between them.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from disputeshield.api.views_management import DisputeViewSet

app_name = "disputeshield"

router = DefaultRouter()
router.register("disputes", DisputeViewSet, basename="dispute")

urlpatterns = [
    path("v1/", include(router.urls)),
]
