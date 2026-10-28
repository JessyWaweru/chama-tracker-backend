from rest_framework.exceptions import NotFound, PermissionDenied

from .models import Chama, Member


def chamas_for(user):
    """Chamas the user is an active member of."""
    return Chama.objects.filter(members__user=user, members__is_active=True).distinct()


def get_membership(user, chama_id):
    """The user's active membership in a chama. 404 (not 403) for outsiders, so ids don't leak."""
    membership = (
        Member.objects.select_related("chama")
        .filter(chama_id=chama_id, user=user, is_active=True)
        .first()
    )
    if membership is None:
        raise NotFound("Chama not found.")
    return membership


def require_official(membership):
    if not membership.is_official:
        raise PermissionDenied("Only the chair, treasurer or secretary can do this.")


def require_treasurer(membership):
    if membership.role != Member.Role.TREASURER:
        raise PermissionDenied("Only the treasurer can record cash or bank contributions.")


class ChamaScopedMixin:
    """Resolves `self.chama` and `self.membership` from the `chama_id` URL kwarg."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.membership = get_membership(request.user, kwargs["chama_id"])
        self.chama = self.membership.chama

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "chama": self.chama}
