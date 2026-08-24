"""Two URL namespaces, kept apart on purpose.

`/v1/widget/*` is session-token scoped and serialises customer-visible fields
only. `/v1/*` is agent scoped. They never share a serializer (§10), and the
leakage test asserts that no field path crosses between them.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from disputeshield.api.views_attachments import AttachmentDownloadView
from disputeshield.api.views_embed import EmbedView
from disputeshield.api.views_health import healthz, readyz
from disputeshield.api.views_management import DisputeViewSet
from disputeshield.api.views_reports import (
    AuditVerifyView,
    RegulatoryReportView,
    SLAPerformanceView,
)
from disputeshield.api.views_widget import (
    SessionView,
    WidgetConfigView,
    WidgetDisputeViewSet,
)

app_name = "disputeshield"

router = DefaultRouter()
router.register("disputes", DisputeViewSet, basename="dispute")

widget_router = DefaultRouter()
widget_router.register("disputes", WidgetDisputeViewSet, basename="widget-dispute")

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("v1/sessions", SessionView.as_view(), name="session-create"),
    path("v1/embed", EmbedView.as_view(), name="embed"),
    path("v1/widget/config", WidgetConfigView.as_view(), name="widget-config"),
    # Signed and expiring; the signature is the authorisation, which is what lets
    # a link be handed to a browser that will not send an API key.
    path(
        "v1/attachments/<str:attachment_id>",
        AttachmentDownloadView.as_view(),
        name="attachment-download",
    ),
    path("v1/widget/", include(widget_router.urls)),
    path("v1/analytics/sla-performance", SLAPerformanceView.as_view(), name="sla-performance"),
    path("v1/reports/regulatory", RegulatoryReportView.as_view(), name="regulatory-report"),
    path("v1/audit/verify", AuditVerifyView.as_view(), name="audit-verify"),
    path("v1/", include(router.urls)),
]
