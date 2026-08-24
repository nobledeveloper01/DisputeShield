"""Uploading and downloading attachments.

The download response is deliberately hostile to a browser: a fixed
`application/octet-stream`, `Content-Disposition: attachment`, `nosniff`, and a
CSP that permits nothing. §10 requires the file be served from a separate origin
and never rendered inline — these headers are what makes "never rendered inline"
true even when it is served from the same host during development.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.attachments import service, storage
from disputeshield.attachments.inspection import MAX_BYTES, RejectedUpload
from disputeshield.models import DisputeAttachment


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisputeAttachment
        fields = (
            "id",
            "filename",
            "content_type",
            "size_bytes",
            "sha256",
            "scan_status",
            "uploaded_by_type",
            "created_at",
        )
        read_only_fields = fields


class CustomerAttachmentSerializer(serializers.ModelSerializer):
    """What the customer sees about their own upload.

    No `sha256`, no `uploaded_by_id`, no scan detail. A scan verdict naming a
    signature tells an uploader which malware got through and which did not.
    """

    class Meta:
        model = DisputeAttachment
        fields = ("id", "filename", "size_bytes", "created_at")
        read_only_fields = fields


def read_upload(request) -> tuple[bytes, str]:
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise RejectedUpload("missing", "No file was sent.")
    if uploaded.size > MAX_BYTES:
        # Checked before reading it into memory. Reading first and rejecting
        # afterwards is how a size limit becomes a memory-exhaustion vector.
        raise RejectedUpload("too_large", f"Files are limited to {MAX_BYTES} bytes.")
    return uploaded.read(), uploaded.name or "attachment"


def rejection_response(exc: RejectedUpload) -> Response:
    return Response(
        {"error": {"type": "rejected_upload", "reason": exc.reason, "message": str(exc)}},
        status=status.HTTP_400_BAD_REQUEST,
    )


class AttachmentDownloadView(APIView):
    """Signed, expiring, and hostile to inline rendering.

    Unauthenticated on purpose: the signature *is* the authorisation, which is
    what lets a link be handed to a browser that will not send an API key. The
    signature is short-lived, single-attachment, and keyed on the server secret.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, attachment_id: str):
        from disputeshield.tenancy.middleware import db_tenant_context

        tenant_id = request.query_params.get("tenant", "")
        try:
            expires = int(request.query_params.get("expires", "0"))
        except ValueError:
            return self._denied()

        # Verified before the tenant is used for anything. The tenant travels in
        # the URL because RLS needs it before the row can be read, and it is
        # covered by the signature so a caller cannot substitute another one.
        try:
            storage.verify(
                attachment_id, tenant_id, expires, request.query_params.get("signature", "")
            )
        except storage.SignatureInvalid:
            return self._denied()

        from django.db import transaction

        with transaction.atomic(), db_tenant_context(tenant_id):
            attachment = DisputeAttachment.objects.all_tenants().filter(pk=attachment_id).first()
            if attachment is None or not attachment.is_retrievable:
                # A pending or infected file is indistinguishable from one that
                # does not exist. Saying "not scanned yet" tells an uploader
                # exactly when to retry.
                return self._denied()
            content = service.retrieve(attachment)
        response = HttpResponse(content, content_type="application/octet-stream")
        # Quoted, and the stored filename is already stripped of anything that
        # would break out of the quotes.
        response["Content-Disposition"] = f'attachment; filename="{attachment.filename}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def _denied(self) -> HttpResponse:
        response = HttpResponse(status=404)
        response["Cache-Control"] = "no-store"
        return response


def download_url(attachment: DisputeAttachment, *, ttl_seconds: int = 300) -> str:
    expires, signature = storage.sign(attachment.pk, attachment.tenant_id, ttl_seconds=ttl_seconds)
    return (
        f"/v1/attachments/{attachment.pk}"
        f"?tenant={attachment.tenant_id}&expires={expires}&signature={signature}"
    )


class AttachmentActionsMixin:
    """Shared upload/list behaviour, with the serializer chosen by the caller."""

    attachment_serializer = AttachmentSerializer
    uploader_type = "agent"

    @action(detail=True, methods=["get", "post"])
    def attachments(self, request, pk=None):
        dispute = self.get_object()

        if request.method == "GET":
            return Response(
                self.attachment_serializer(
                    dispute.attachments.filter(scan_status=DisputeAttachment.ScanStatus.CLEAN),
                    many=True,
                ).data
            )

        try:
            content, filename = read_upload(request)
        except RejectedUpload as exc:
            return rejection_response(exc)

        def produce():
            try:
                attachment = service.upload(
                    dispute=dispute,
                    content=content,
                    filename=filename,
                    uploaded_by_type=self.uploader_type,
                    uploaded_by_id=self._uploader_id(request),
                )
            except RejectedUpload as exc:
                return rejection_response(exc)
            return Response(self.attachment_serializer(attachment).data, status=201)

        return self.idempotent(request, "dispute.attachment", produce)

    def _uploader_id(self, request) -> str:
        agent = getattr(request, "acting_agent", None)
        return agent.pk if agent else ""
