"""
Coverage area utility functions for the accounts app.

These helpers are shared across event management and future phases.
"""
from django.db.models import Q

from .models import Account, UserCoverageArea


def get_account_associations(account):
    """
    Return a dictionary of all data associated to the given account with counts.

    This is the single centralized place where account association checks live.
    The deletion check, blocking messages, and any future account-related features
    should all call this function rather than querying associations ad hoc.

    When new association types are added in the future (e.g. CRM notes, contacts),
    add only one new entry here and all callers automatically pick it up.

    Returns:
        dict mapping association name → count, e.g.:
        {'events': 3, 'photos': 0}
    """
    from apps.events.models import Event, EventPhoto

    return {
        'events': Event.objects.filter(account=account).count(),
        'photos': EventPhoto.objects.filter(account=account).count(),
    }


def get_accounts_for_user(user):
    """
    Return a queryset of active accounts visible to user based on their
    coverage areas (union logic).

    Final visibility rules:
      - SaaS Admin:      all accounts across all companies (no company filter)
      - Supplier Admin:  all active accounts for their company (no coverage filter)
      - All other roles: coverage area union logic; empty queryset if no areas assigned
        (this includes Sales Manager, TM, AM, Ambassador, Distributor Contact)

    A user's coverage areas define which accounts they see. The result is the
    union of all accounts matching ANY of their coverage area entries:
      - Distributor coverage → all accounts under that distributor
      - State coverage      → all accounts in that state
      - County coverage     → all accounts in that county + state
      - City coverage       → all accounts in that city + state
      - Account coverage    → that specific account directly
    """
    if user.has_role('saas_admin'):
        # SaaS Admin sees all accounts across all companies
        return Account.active_accounts.all()

    company = user.company
    if not company:
        return Account.active_accounts.none()

    if user.has_role('supplier_admin'):
        # Supplier Admin sees all accounts for their company
        return Account.active_accounts.filter(company=company)

    # All other roles: coverage area union logic
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

    Special rule for Supplier Admin:
      - If 'supplier_admin' is in roles, Supplier Admins are always included
        for any account regardless of coverage area.

    For all other roles, uses the same union logic as get_accounts_for_user
    but in reverse: checks whether each user has at least one coverage area
    that covers the account.

    An ambassador/TM/AM/SM covers an account if ANY of these are true:
      - They have a Distributor coverage matching the account's distributor
      - They have a State coverage matching the account's state_normalized
      - They have a County coverage matching account's county + state_normalized
      - They have a City coverage matching account's city + state_normalized
      - They have an Account coverage for this account directly

    Args:
        account: Account instance
        roles:   iterable of role strings (e.g. ['ambassador', 'territory_manager'])

    Returns:
        QuerySet of User instances ordered by last_name, first_name
    """
    from apps.core.models import User as UserModel

    company = account.company
    roles = list(roles)

    # Separate Supplier Admin (always included) from coverage-filtered roles
    include_supplier_admin = 'supplier_admin' in roles
    filtered_roles = [r for r in roles if r != 'supplier_admin']

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

    # Coverage-filtered users
    result_qs = UserModel.objects.none()

    if filtered_roles:
        covering_user_pks = (
            UserCoverageArea.objects.filter(company=company)
            .filter(coverage_q)
            .values_list('user_id', flat=True)
            .distinct()
        )
        coverage_filtered = UserModel.objects.filter(
            company=company,
            roles__codename__in=filtered_roles,
            is_active=True,
            pk__in=covering_user_pks,
        ).distinct()
        result_qs = coverage_filtered

    if include_supplier_admin:
        supplier_admins = UserModel.objects.filter(
            company=company,
            roles__codename='supplier_admin',
            is_active=True,
        )
        result_qs = result_qs | supplier_admins

    return result_qs.order_by('last_name', 'first_name').distinct()
