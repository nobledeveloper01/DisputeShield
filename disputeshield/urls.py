from django.urls import path

app_name = "disputeshield"

# Routes land per phase (docs/ROADMAP.md). Two URL namespaces are kept separate
# throughout: /v1/widget/* is session-token scoped and serialises customer-visible
# fields only; /v1/* is agent scoped. They never share a serializer (§10).
urlpatterns: list[path] = []
