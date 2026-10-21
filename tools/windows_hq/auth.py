"""Proxy identity sign-in, revalidated on every request, with no password path."""

import hmac
import os

from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import BaseBackend
from django.http import HttpResponseForbidden
from django.utils import timezone


class TailnetBackend(BaseBackend):
    def get_user(self, user_id):
        return get_user_model().objects.filter(pk=user_id, is_active=True).first()


class TailnetAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/live/":
            return self.get_response(request)
        identity = request.headers.get("Tailscale-User-Login", "")
        allowed = os.environ["GAMEDEV_OPERATOR_LOGIN"]
        if not hmac.compare_digest(identity.encode(), allowed.encode()):
            return HttpResponseForbidden("Use Tailscale Serve as the configured workspace operator.")
        user = get_user_model().objects.filter(username=allowed, is_active=True).first()
        if user is None:
            return HttpResponseForbidden("Workspace operator is not provisioned or is inactive.")
        if not request.user.is_authenticated or request.user.pk != user.pk:
            login(request, user, backend="tools.windows_hq.auth.TailnetBackend")
            request.session["signed_in_at"] = timezone.now().isoformat()
            request.session["oidc_tailscale_principal"] = identity
        return self.get_response(request)
