"""
Core middleware for productERP.
"""
import sentry_sdk


class SentryCompanyTagMiddleware:
    """
    Tags the Sentry scope with the authenticated user's company slug so that
    errors in Sentry are filterable and triageable per tenant.

    Safe no-ops for:
    - Anonymous / unauthenticated requests (no company tag set).
    - Authenticated users with no company (e.g. saas_admin, company=None).
    - Requests processed when Sentry is not initialised (sentry_sdk.set_tag
      is a documented no-op when init() has not been called).

    Must appear after AuthenticationMiddleware in MIDDLEWARE so that
    request.user is populated before this runs.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            if (
                request.user.is_authenticated
                and request.user.company_id is not None
            ):
                sentry_sdk.set_tag('company', request.user.company.slug)
        except Exception:
            pass
        return self.get_response(request)
