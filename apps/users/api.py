from ninja import Router
from ninja.security import django_auth
from django.contrib.auth import logout as django_logout

from .schemas import UserOut

router = Router(tags=["auth"])


@router.post("/logout", auth=django_auth)
def logout(request):
    django_logout(request)
    return {"success": True}


@router.get("/me", response=UserOut, auth=django_auth)
def me(request):
    return request.auth