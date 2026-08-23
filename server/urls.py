from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("disputeshield.urls")),
    # A separate, more restricted surface (§6.5): rarely-changed configuration
    # only, behind SSO and TOTP, IP-restricted at the edge.
    path("admin/", admin.site.urls),
]
