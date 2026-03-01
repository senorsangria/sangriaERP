"""
Integration tests for the events app — Phase 10.3.1.

Covers:
  - Admin event release: Draft → Recap Submitted (not Scheduled)
  - Non-admin event release: Draft → Scheduled (unchanged)
  - Release validation: date and ambassador required
  - Request Revision blocked for Admin events (view + template)
  - Items section visibility gated by event status
  - Permission enforcement on status transitions
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.events.models import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name="Test Beverage Co"):
    return Company.objects.create(name=name)


def make_user(company, role, username="testuser"):
    return User.objects.create_user(
        username=username,
        password="testpass123",
        company=company,
        role=role,
    )


def make_account(company, name="Test Liquors"):
    return Account.objects.create(
        company=company,
        name=name,
        street="1 Main St",
        city="Hoboken",
        state="NJ",
    )


def make_item(company, item_code="Red0750"):
    brand, _ = Brand.objects.get_or_create(company=company, name="Test Brand")
    return Item.objects.create(brand=brand, name="Test Item", item_code=item_code)


def make_event(company, creator, event_type, status=Event.Status.DRAFT, **kwargs):
    return Event.objects.create(
        company=company,
        created_by=creator,
        event_manager=creator,
        event_type=event_type,
        status=status,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Admin event release: Draft → Recap Submitted
# ---------------------------------------------------------------------------

class AdminEventReleaseTransitionTest(TestCase):
    """
    Admin events have no recap step.
    Release should send them to Recap Submitted, not Scheduled.
    """

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_admin_event_release_goes_to_recap_submitted(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_admin_event_release_does_not_go_to_scheduled(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertNotEqual(event.status, Event.Status.SCHEDULED)

    def test_tasting_event_release_goes_to_scheduled(self):
        account = make_account(self.company)
        item = make_item(self.company)
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador, account=account,
        )
        event.items.add(item)
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_festival_event_release_goes_to_scheduled(self):
        account = make_account(self.company, name="Festival Venue")
        event = make_event(
            self.company, self.manager, Event.EventType.FESTIVAL,
            date=date.today(), ambassador=self.ambassador, account=account,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)


# ---------------------------------------------------------------------------
# Release validation
# ---------------------------------------------------------------------------

class EventReleaseValidationTest(TestCase):
    """Release requires date and ambassador regardless of event type."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_release_blocked_without_date(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            ambassador=self.ambassador,  # no date
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_release_blocked_without_ambassador(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(),  # no ambassador
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_release_blocked_on_non_draft_event(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_tasting_release_blocked_without_account(self):
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador,  # no account
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)


# ---------------------------------------------------------------------------
# Release permission enforcement
# ---------------------------------------------------------------------------

class EventReleasePermissionTest(TestCase):
    """Only ACTION_ROLES may release events."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")

    def _release_as(self, role_user, event):
        c = Client()
        c.login(username=role_user.username, password="testpass123")
        c.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()

    def test_ambassador_cannot_release(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        self._release_as(self.ambassador, event)
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_supplier_admin_can_release(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        self._release_as(self.manager, event)
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)


# ---------------------------------------------------------------------------
# Admin event approve: Recap Submitted → Complete
# ---------------------------------------------------------------------------

class AdminEventApproveTest(TestCase):
    """Admin events in Recap Submitted status can be approved directly."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_admin_event_approve_completes_event(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(reverse("event_approve", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.COMPLETE)


# ---------------------------------------------------------------------------
# Request Revision blocked for Admin events
# ---------------------------------------------------------------------------

class RequestRevisionAdminBlockTest(TestCase):
    """Request Revision must be blocked for Admin events (view + template)."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_request_revision_view_blocked_for_admin_event(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(
            reverse("event_request_revision", args=[event.pk]),
            {"revision_note": "Please fix something"},
        )
        event.refresh_from_db()
        # Status must NOT change to Revision Requested
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_request_revision_button_absent_for_admin_event(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Request Revision")

    def test_request_revision_button_present_for_tasting_event(self):
        account = make_account(self.company)
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador, account=account,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Request Revision")

    def test_request_revision_view_works_for_tasting_event(self):
        account = make_account(self.company)
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(), ambassador=self.ambassador, account=account,
        )
        self.client.post(
            reverse("event_request_revision", args=[event.pk]),
            {"revision_note": "Please fix the sample count"},
        )
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.REVISION_REQUESTED)
        self.assertEqual(event.revision_note, "Please fix the sample count")


# ---------------------------------------------------------------------------
# Items section visibility gated by status
# ---------------------------------------------------------------------------

class ItemsSectionVisibilityTest(TestCase):
    """
    Items section is visible in Draft and Scheduled.
    Hidden once the recap workflow is active (Recap Submitted onward).
    """

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.account = make_account(self.company)
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def _tasting_event(self, status):
        return make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=status,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )

    def test_items_shown_in_draft(self):
        event = self._tasting_event(Event.Status.DRAFT)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Items to be Sampled")

    def test_items_shown_in_scheduled(self):
        event = self._tasting_event(Event.Status.SCHEDULED)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Items to be Sampled")

    def test_items_hidden_in_recap_submitted(self):
        event = self._tasting_event(Event.Status.RECAP_SUBMITTED)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Items to be Sampled")

    def test_items_hidden_in_revision_requested(self):
        event = self._tasting_event(Event.Status.REVISION_REQUESTED)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Items to be Sampled")

    def test_items_hidden_in_complete(self):
        event = self._tasting_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Items to be Sampled")


# ---------------------------------------------------------------------------
# Event detail layout — top bar and schedule in location card
# ---------------------------------------------------------------------------

class EventDetailLayoutTest(TestCase):
    """Smoke tests confirming the new UI layout renders correctly."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_event_type_badge_in_response(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Admin")  # event type badge

    def test_status_badge_in_response(self):
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Draft")  # status badge

    def test_date_formatted_mm_dd_yy(self):
        from datetime import date as d
        today = d(2026, 2, 28)
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=today, ambassador=self.ambassador,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "02/28/26")

    def test_admin_event_no_start_time_shown(self):
        """Start time section must not render for Admin events."""
        from datetime import time
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
            start_time=time(13, 0),  # should be ignored
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        # The "Start Time" info-label must not appear for admin events
        self.assertNotContains(resp, "Start Time")

    def test_revision_note_displayed_when_revision_requested(self):
        account = make_account(self.company)
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.REVISION_REQUESTED,
            date=date.today(), ambassador=self.ambassador, account=account,
            revision_note="Please correct the sample count.",
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Please correct the sample count.")


# ---------------------------------------------------------------------------
# Tasting event release — items required
# ---------------------------------------------------------------------------

class TastingReleaseItemsRequiredTest(TestCase):
    """
    A Tasting event cannot be released unless at least one item is associated.
    """

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, User.Role.SUPPLIER_ADMIN, "manager")
        self.ambassador = make_user(self.company, User.Role.AMBASSADOR, "amb")
        self.account = make_account(self.company)
        self.client = Client()
        self.client.login(username="manager", password="testpass123")

    def test_tasting_release_blocked_without_items(self):
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        # No items associated — release must be blocked
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_tasting_release_blocked_message_mentions_items(self):
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        response = self.client.post(
            reverse("event_release", args=[event.pk]),
            follow=True,
        )
        self.assertContains(response, "item")

    def test_tasting_release_succeeds_with_one_item(self):
        item = make_item(self.company)
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        event.items.add(item)
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_tasting_release_succeeds_with_multiple_items(self):
        item1 = make_item(self.company, item_code="Red0750")
        item2 = make_item(self.company, item_code="Wht0750")
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        event.items.add(item1, item2)
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_festival_release_not_blocked_without_items(self):
        """Festival events do not require items to release."""
        event = make_event(
            self.company, self.manager, Event.EventType.FESTIVAL,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_admin_release_not_blocked_without_items(self):
        """Admin events do not require items to release."""
        event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(), ambassador=self.ambassador,
        )
        self.client.post(reverse("event_release", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)
