"""
Coverage area utility functions for the accounts app.

These helpers are shared across event management and future phases.
"""
from django.db.models import Q

from .models import Account, UserCoverageArea


def get_accounts_for_user(user):
    """
    Return a queryset of active accounts visible to user based on their
    coverage areas (union logic).

    A user's coverage areas define which accounts they see. The result is the
    union of all accounts matching ANY of their coverage area entries:
      - Distributor coverage → all accounts under that distributor
      - State coverage      → all accounts in that state
      - County coverage     → all accounts in that county + state
      - City coverage       → all accounts in that city + state
      - Account coverage    → that specific account directly

    Supplier Admin and Sales Manager always see all company accounts.
    """
    from apps.core.models import User as UserModel

    company = user.company
    if not company:
        return Account.active_accounts.none()

    if user.role in (UserModel.Role.SUPPLIER_ADMIN, UserModel.Role.SALES_MANAGER):
        return Account.active_accounts.filter(company=company)

    coverage_areas = list(
        UserCoverageArea.objects.filter(user=user, company=company)
        .select_related('distributor', 'account')
    )

    if not coverage_areas:
        return Account.active_accounts.none()

    # Build union Q across all coverage areas
    q = Q(pk__in=[])  # start with an empty match
    for ca in coverage_areas:
        ct = ca.coverage_type
        if ct == UserCoverageArea.CoverageType.DISTRIBUTOR and ca.distributor_id:
            q |= Q(distributor_id=ca.distributor_id)
        elif ct == UserCoverageArea.CoverageType.STATE and ca.state:
            q |= Q(state_normalized=ca.state)
        elif ct == UserCoverageArea.CoverageType.COUNTY and ca.county and ca.state:
            q |= Q(county=ca.county, state_normalized=ca.state)
        elif ct == UserCoverageArea.CoverageType.CITY and ca.city and ca.state:
            q |= Q(city=ca.city, state_normalized=ca.state)
        elif ct == UserCoverageArea.CoverageType.ACCOUNT and ca.account_id:
            q |= Q(pk=ca.account_id)

    return Account.active_accounts.filter(company=company).filter(q)


def get_users_covering_account(account, roles):
    """
    Return a queryset of users with the given roles whose coverage areas
    include the given account.

    Uses the same union logic as get_accounts_for_user but in reverse: checks
    whether each user has at least one coverage area that covers the account.

    An ambassador/TM/AM covers an account if ANY of these are true:
      - They have a Distributor coverage matching the account's distributor
      - They have a State coverage matching the account's state_normalized
      - They have a County coverage matching account's county + state_normalized
      - They have a City coverage matching account's city + state_normalized
      - They have an Account coverage for this account directly

    Args:
        account: Account instance
        roles:   iterable of role strings (e.g. ['ambassador', 'ambassador_manager'])

    Returns:
        QuerySet of User instances ordered by last_name, first_name
    """
    from apps.core.models import User as UserModel

    company = account.company

    # Build Q matching UserCoverageArea records that cover this account
    coverage_q = Q(pk__in=[])

    if account.distributor_id:
        coverage_q |= Q(
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor_id=account.distributor_id,
        )
    if account.state_normalized:
        coverage_q |= Q(
            coverage_type=UserCoverageArea.CoverageType.STATE,
            state=account.state_normalized,
        )
    if account.county and account.county not in ('', 'Unknown'):
        coverage_q |= Q(
            coverage_type=UserCoverageArea.CoverageType.COUNTY,
            county=account.county,
            state=account.state_normalized,
        )
    if account.city:
        coverage_q |= Q(
            coverage_type=UserCoverageArea.CoverageType.CITY,
            city=account.city,
            state=account.state_normalized,
        )
    coverage_q |= Q(
        coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
        account_id=account.pk,
    )

    covering_user_pks = (
        UserCoverageArea.objects.filter(company=company)
        .filter(coverage_q)
        .values_list('user_id', flat=True)
        .distinct()
    )

    return (
        UserModel.objects.filter(
            company=company,
            role__in=roles,
            is_active=True,
        )
        .filter(pk__in=covering_user_pks)
        .order_by('last_name', 'first_name')
    )
