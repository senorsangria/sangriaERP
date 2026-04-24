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
from apps.distribution.models import Distributor
from apps.events.models import Event, Expense, EventPhoto


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name="Test Beverage Co"):
    return Company.objects.create(name=name)


def make_distributor(company, name="Test Distributor"):
    return Distributor.objects.create(company=company, name=name)


def make_user(company, role_codename, username="testuser"):
    from apps.core.rbac import Role
    user = User.objects.create_user(
        username=username,
        password="testpass123",
        company=company,
    )
    role = Role.objects.get(codename=role_codename)
    user.roles.set([role])
    return user


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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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

    def test_special_event_release_goes_to_scheduled(self):
        account = make_account(self.company, name="Special Event Venue")
        event = make_event(
            self.company, self.manager, Event.EventType.SPECIAL_EVENT,
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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")

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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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

    def test_items_hidden_in_scheduled(self):
        event = self._tasting_event(Event.Status.SCHEDULED)
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Items to be Sampled")

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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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
        self.manager = make_user(self.company, 'supplier_admin', "manager")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
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

    def test_special_event_release_not_blocked_without_items(self):
        """Special Event events do not require items to release."""
        event = make_event(
            self.company, self.manager, Event.EventType.SPECIAL_EVENT,
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


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Recap form: save, submit, unlock, price updates
# ---------------------------------------------------------------------------

from apps.accounts.models import AccountItem, AccountItemPriceHistory, UserCoverageArea
from apps.distribution.models import Distributor
from apps.events.models import EventItemRecap, EventPhoto


class RecapSaveTest(TestCase):
    """event_save_recap: saves data, advances Scheduled → Recap In Progress on first save."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.item = make_item(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.event.items.add(self.item)
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_scheduled_to_recap_in_progress_on_first_save(self):
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "samples_poured": "10",
            "qr_codes_scanned": "5",
            "recap_notes": "Went well",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_recap_data_saved_on_save(self):
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "samples_poured": "25",
            "qr_codes_scanned": "3",
            "recap_notes": "Great turnout",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_samples_poured, 25)
        self.assertEqual(self.event.recap_qr_codes_scanned, 3)
        self.assertEqual(self.event.recap_notes, "Great turnout")

    def test_status_stays_recap_in_progress_on_subsequent_save(self):
        self.event.status = Event.Status.RECAP_IN_PROGRESS
        self.event.save()
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "samples_poured": "10",
            "recap_notes": "Updated notes",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_item_recap_created_on_save(self):
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "samples_poured": "10",
            f"shelf_price_{self.item.pk}": "14.99",
            f"bottles_sold_{self.item.pk}": "3",
            f"bottles_samples_{self.item.pk}": "1",
        })
        recap = EventItemRecap.objects.get(event=self.event, item=self.item)
        self.assertEqual(recap.shelf_price, round_decimal("14.99"))
        self.assertEqual(recap.bottles_sold, 3)
        self.assertEqual(recap.bottles_used_for_samples, 1)

    def test_save_blocked_for_non_recap_user(self):
        """A Sales Manager with no coverage area cannot see the event (404)."""
        other = make_user(self.company, 'sales_manager', "other")
        c = Client()
        c.login(username="other", password="testpass123")
        response = c.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_notes": "Should not save",
        })
        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_notes, "")

    def test_save_blocked_when_status_not_active(self):
        """Save is rejected when status is Complete."""
        self.event.status = Event.Status.COMPLETE
        self.event.save()
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_notes": "Should not save",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_notes, "")


def round_decimal(s):
    from decimal import Decimal
    return Decimal(s)


class RecapSubmitTest(TestCase):
    """event_submit_recap: saves data, updates prices, moves to Recap Submitted."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.item = make_item(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_IN_PROGRESS,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.event.items.add(self.item)
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_submit_moves_to_recap_submitted(self):
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "All done",
            f"shelf_price_{self.item.pk}": "12.99",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)

    def test_submit_with_empty_fields_succeeds(self):
        """Submit is allowed with any combination of filled or empty fields."""
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)

    def test_submit_updates_account_item_price(self):
        # Pre-create AccountItem with no price
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=date.today(),
        )
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "Done",
            f"shelf_price_{self.item.pk}": "14.99",
        })
        ai.refresh_from_db()
        self.assertEqual(ai.current_price, round_decimal("14.99"))

    def test_submit_archives_changed_price_to_history(self):
        from decimal import Decimal
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=date.today(),
            current_price=Decimal("9.99"),
        )
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "Price changed",
            f"shelf_price_{self.item.pk}": "12.99",
        })
        ai.refresh_from_db()
        self.assertEqual(ai.current_price, round_decimal("12.99"))
        history = AccountItemPriceHistory.objects.filter(account_item=ai)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().price, round_decimal("9.99"))

    def test_submit_no_history_if_price_unchanged(self):
        from decimal import Decimal
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=date.today(),
            current_price=Decimal("12.99"),
        )
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "Same price",
            f"shelf_price_{self.item.pk}": "12.99",
        })
        self.assertEqual(AccountItemPriceHistory.objects.count(), 0)

    def test_submit_from_revision_requested_goes_to_recap_submitted(self):
        self.event.status = Event.Status.REVISION_REQUESTED
        self.event.recap_notes = "Previously saved"
        self.event.save()
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_notes": "Fixed per revision",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)


class RecapUnlockTest(TestCase):
    """event_unlock_recap: Recap Submitted → Recap In Progress."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_unlock_moves_to_recap_in_progress(self):
        self.client.post(reverse("event_unlock_recap", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_unlock_blocked_when_not_recap_submitted(self):
        self.event.status = Event.Status.SCHEDULED
        self.event.save()
        self.client.post(reverse("event_unlock_recap", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_unlock_blocked_for_non_recap_user(self):
        other = make_user(self.company, 'sales_manager', "other")
        c = Client()
        c.login(username="other", password="testpass123")
        response = c.post(reverse("event_unlock_recap", args=[self.event.pk]))
        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)

    def test_ambassador_can_unlock(self):
        c = Client()
        c.login(username="amb", password="testpass123")
        c.post(reverse("event_unlock_recap", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)


class SpecialEventRecapTest(TestCase):
    """Special Event recap: comment + photos only (no per-item)."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.SPECIAL_EVENT,
            status=Event.Status.SCHEDULED,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_special_event_save_saves_comment(self):
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_comment": "Great special event!",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_comment, "Great special event!")
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_special_event_submit_with_empty_fields_succeeds(self):
        """Submit is now allowed with empty fields (no minimum requirement)."""
        self.event.status = Event.Status.RECAP_IN_PROGRESS
        self.event.save()
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_comment": "",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)

    def test_special_event_submit_with_comment_succeeds(self):
        self.event.status = Event.Status.RECAP_IN_PROGRESS
        self.event.save()
        self.client.post(reverse("event_submit_recap", args=[self.event.pk]), {
            "recap_comment": "Great event!",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_SUBMITTED)


class RecapAccessRulesTest(TestCase):
    """_can_recap: verify access for different user types."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.other_user = make_user(self.company, 'sales_manager', "other")
        self.account = make_account(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.client = Client()

    def test_assigned_ambassador_can_save_recap(self):
        self.client.login(username="amb", password="testpass123")
        self.event.items.add(make_item(self.company))
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_notes": "Ambassador saving",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_event_manager_can_save_recap(self):
        self.event.event_manager = self.manager
        self.event.save()
        self.client.login(username="mgr", password="testpass123")
        self.event.items.add(make_item(self.company))
        self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_notes": "Manager saving",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.RECAP_IN_PROGRESS)

    def test_unrelated_user_cannot_save_recap(self):
        """A Sales Manager with no coverage area cannot see the event (404)."""
        self.client.login(username="other", password="testpass123")
        response = self.client.post(reverse("event_save_recap", args=[self.event.pk]), {
            "recap_notes": "Unauthorized",
        })
        self.assertEqual(response.status_code, 404)

    def test_admin_event_has_no_recap(self):
        """_can_recap returns False for Admin events."""
        from apps.events.views import _can_recap
        admin_event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.SCHEDULED,
        )
        self.assertFalse(_can_recap(self.ambassador, admin_event))


class ApproveRaceConditionTest(TestCase):
    """event_approve: race condition guard prevents double-approval."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_SUBMITTED,
            date=date.today(),
            ambassador=self.ambassador,
            account=self.account,
        )
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_approve_recap_submitted_goes_to_complete(self):
        self.client.post(reverse("event_approve", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.COMPLETE)

    def test_approve_blocked_when_not_recap_submitted(self):
        self.event.status = Event.Status.RECAP_IN_PROGRESS
        self.event.save()
        self.client.post(reverse("event_approve", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.status, Event.Status.COMPLETE)


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Duration display format ('hr' not 'h')
# ---------------------------------------------------------------------------

class EventDurationDisplayTest(TestCase):
    """Event.duration_display uses 'hr' suffix, not 'h'."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.event = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            date=date.today(),
        )

    def test_hours_only(self):
        self.event.duration_hours = 2
        self.event.duration_minutes = None
        self.assertEqual(self.event.duration_display, "2hr")

    def test_hours_and_minutes(self):
        self.event.duration_hours = 2
        self.event.duration_minutes = 30
        self.assertEqual(self.event.duration_display, "2hr 30m")

    def test_minutes_only(self):
        self.event.duration_hours = None
        self.event.duration_minutes = 45
        self.assertEqual(self.event.duration_display, "45m")

    def test_no_duration(self):
        self.event.duration_hours = None
        self.event.duration_minutes = None
        self.assertEqual(self.event.duration_display, "—")


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Items hidden in Recap In Progress
# ---------------------------------------------------------------------------

class ItemsSectionRecapInProgressTest(TestCase):
    """Items section must be hidden when status is Recap In Progress."""

    def setUp(self):
        self.company = make_company()
        self.manager = make_user(self.company, 'supplier_admin', "mgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.client = Client()
        self.client.login(username="mgr", password="testpass123")

    def test_items_hidden_in_recap_in_progress(self):
        event = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.RECAP_IN_PROGRESS,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        resp = self.client.get(reverse("event_detail", args=[event.pk]))
        self.assertNotContains(resp, "Items to be Sampled")


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Revert Complete → Recap Submitted
# ---------------------------------------------------------------------------

class EventRevertCompleteTest(TestCase):
    """event_revert_complete: Complete → Recap Submitted for authorised users."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.sales_mgr = make_user(self.company, 'sales_manager', "salesmgr")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.distributor = make_distributor(self.company)
        UserCoverageArea.objects.create(
            user=self.sales_mgr, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )

    def _complete_event(self, event_manager=None):
        event = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            status=Event.Status.COMPLETE,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        if event_manager:
            event.event_manager = event_manager
            event.save(update_fields=['event_manager'])
        return event

    def _revert_as(self, username, event):
        c = Client()
        c.login(username=username, password="testpass123")
        c.post(reverse("event_revert_complete", args=[event.pk]))
        event.refresh_from_db()

    def test_supplier_admin_can_revert(self):
        event = self._complete_event()
        self._revert_as("admin", event)
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_sales_manager_can_revert(self):
        event = self._complete_event()
        self._revert_as("salesmgr", event)
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_assigned_event_manager_can_revert(self):
        """An Ambassador Manager who is the assigned event manager can revert."""
        event_mgr = make_user(self.company, 'ambassador_manager', "evtmgr")
        event = self._complete_event(event_manager=event_mgr)
        self._revert_as("evtmgr", event)
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_unassigned_ambassador_manager_cannot_revert(self):
        """An Ambassador Manager not assigned as event manager cannot revert."""
        other = make_user(self.company, 'ambassador_manager', "other")
        event = self._complete_event()
        self._revert_as("other", event)
        self.assertEqual(event.status, Event.Status.COMPLETE)

    def test_revert_blocked_for_non_complete_event(self):
        """Can only revert Complete events — Scheduled stays Scheduled."""
        event = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        c = Client()
        c.login(username="admin", password="testpass123")
        c.post(reverse("event_revert_complete", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_revert_button_visible_for_admin_on_complete_event(self):
        event = self._complete_event()
        c = Client()
        c.login(username="admin", password="testpass123")
        resp = c.get(reverse("event_detail", args=[event.pk]))
        self.assertContains(resp, "Revert to Recap Submitted")


# ---------------------------------------------------------------------------
# Phase 10.3.3 Tweaks — Photo Delete
# ---------------------------------------------------------------------------

class EventPhotoDeleteTest(TestCase):
    """event_photo_delete: AJAX delete of a single EventPhoto record."""

    def setUp(self):
        from apps.events.models import EventPhoto
        self.EventPhoto = EventPhoto

        self.company  = make_company()
        self.admin    = make_user(self.company, 'supplier_admin', "admin")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account  = make_account(self.company)
        self.brand    = Brand.objects.create(company=self.company, name="TestBrand")
        self.distributor = make_distributor(self.company)

        # Tasting event in Recap In Progress with ambassador assigned
        from apps.accounts.models import UserCoverageArea
        UserCoverageArea.objects.create(
            user=self.ambassador, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )
        self.event = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            status=Event.Status.RECAP_IN_PROGRESS,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )
        self.photo = EventPhoto.objects.create(
            event=self.event,
            account=self.account,
            file_url='/media/events/test/test.jpg',
            uploaded_by=self.ambassador,
        )

    def _delete(self, username, photo=None):
        if photo is None:
            photo = self.photo
        c = Client()
        c.login(username=username, password="testpass123")
        return c.post(reverse("event_photo_delete", args=[self.event.pk, photo.pk]),
                      HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_ambassador_can_delete_photo(self):
        resp = self._delete("amb")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.assertFalse(self.EventPhoto.objects.filter(pk=self.photo.pk).exists())

    def test_supplier_admin_can_delete_photo(self):
        from apps.accounts.models import UserCoverageArea
        UserCoverageArea.objects.create(
            user=self.admin, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )
        resp = self._delete("admin")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

    def test_delete_blocked_when_recap_submitted(self):
        self.event.status = Event.Status.RECAP_SUBMITTED
        self.event.save(update_fields=['status'])
        resp = self._delete("amb")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(self.EventPhoto.objects.filter(pk=self.photo.pk).exists())

    def test_delete_allowed_when_revision_requested(self):
        self.event.status = Event.Status.REVISION_REQUESTED
        self.event.save(update_fields=['status'])
        resp = self._delete("amb")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

    def test_delete_requires_post(self):
        c = Client()
        c.login(username="amb", password="testpass123")
        resp = c.get(reverse("event_photo_delete", args=[self.event.pk, self.photo.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_unrelated_user_cannot_delete(self):
        stranger = make_user(self.company, 'sales_manager', "stranger")
        resp = self._delete("stranger")
        # Sales Manager without coverage area cannot see the event (404)
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(self.EventPhoto.objects.filter(pk=self.photo.pk).exists())

# ---------------------------------------------------------------------------
# Unrelease (Scheduled → Draft) permission tests
# ---------------------------------------------------------------------------

class UnreleasePermissionTest(TestCase):
    """
    POST /events/<pk>/unrelease/ should be allowed for Supplier Admin,
    Sales Manager, and the assigned Event Manager; blocked for all others.
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.sales = make_user(self.company, 'sales_manager', "sales")
        self.amb_mgr = make_user(self.company, 'ambassador_manager', "ambmgr")
        self.amb = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.distributor = make_distributor(self.company)
        UserCoverageArea.objects.create(
            user=self.sales, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )

    def _make_scheduled(self, event_manager=None):
        em = event_manager or self.admin
        return Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_manager=em,
            event_type=Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            ambassador=self.amb,
            account=self.account,
            date=date(2026, 6, 1),
        )

    def _post(self, username, event):
        c = Client()
        c.login(username=username, password="testpass123")
        return c.post(reverse("event_unrelease", args=[event.pk]))

    def test_supplier_admin_can_unrelease(self):
        event = self._make_scheduled()
        resp = self._post("admin", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_sales_manager_can_unrelease(self):
        event = self._make_scheduled()
        resp = self._post("sales", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_assigned_event_manager_can_unrelease(self):
        """An Ambassador Manager who is the assigned event_manager can unrelease.

        After unrelease the event becomes a Draft that the ambassador manager
        did not create, so they can no longer access the detail page — only
        check the redirect itself (302), not its destination.
        """
        event = self._make_scheduled(event_manager=self.amb_mgr)
        resp = self._post("ambmgr", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]),
                             fetch_redirect_response=False)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_non_assigned_amb_manager_cannot_unrelease(self):
        """An Ambassador Manager who is NOT the event_manager cannot see the event (404)."""
        other_mgr = make_user(self.company, 'ambassador_manager', "othermgr")
        event = self._make_scheduled(event_manager=self.admin)
        resp = self._post("othermgr", event)
        # Event is not visible to this user → 404 (also prevents the action)
        self.assertEqual(resp.status_code, 404)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_ambassador_cannot_unrelease(self):
        event = self._make_scheduled()
        resp = self._post("amb", event)
        self.assertEqual(resp.status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_wrong_status_rejected(self):
        """Trying to unrelease a Draft event returns an error."""
        event = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            status=Event.Status.DRAFT,
            ambassador=self.amb,
            account=self.account,
            date=date(2026, 6, 1),
        )
        resp = self._post("admin", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)  # unchanged

# ---------------------------------------------------------------------------
# CSV Export column tests
# ---------------------------------------------------------------------------

class CsvExportColumnsTest(TestCase):
    """
    Verify the CSV export includes Ambassador, Event Manager, QR Codes Scanned,
    and Recap Note columns in the correct positions.
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.amb = make_user(self.company, 'ambassador', "amb")
        self.amb.first_name = "Jane"
        self.amb.last_name = "Smith"
        self.amb.save()
        self.mgr = make_user(self.company, 'sales_manager', "mgr")
        self.mgr.first_name = "Bob"
        self.mgr.last_name = "Jones"
        self.mgr.save()
        self.account = make_account(self.company)
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def _get_csv(self):
        resp = self.client.get(reverse("event_export_csv"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        import csv, io
        reader = csv.reader(io.StringIO(content))
        return list(reader)

    def test_header_includes_new_columns(self):
        rows = self._get_csv()
        header = rows[0]
        self.assertIn('Ambassador', header)
        self.assertIn('Event Manager', header)
        self.assertIn('QR Codes Scanned', header)
        self.assertIn('Recap Note', header)

    def test_column_order(self):
        rows = self._get_csv()
        header = rows[0]
        city_idx       = header.index('City')
        amb_idx        = header.index('Ambassador')
        mgr_idx        = header.index('Event Manager')
        samples_idx    = header.index('Samples Poured')
        qr_idx         = header.index('QR Codes Scanned')
        recap_idx      = header.index('Recap Note')
        self.assertEqual(amb_idx, city_idx + 1)
        self.assertEqual(mgr_idx, amb_idx + 1)
        self.assertEqual(samples_idx, mgr_idx + 1)
        self.assertEqual(qr_idx, samples_idx + 1)
        self.assertEqual(recap_idx, len(header) - 1)  # last column

    def test_ambassador_and_manager_names_in_row(self):
        Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_type=Event.EventType.TASTING,
            status=Event.Status.COMPLETE,
            ambassador=self.amb,
            event_manager=self.mgr,
            account=self.account,
            date=date(2026, 5, 1),
            recap_samples_poured=10,
            recap_qr_codes_scanned=5,
            recap_notes="Great event",
        )
        rows = self._get_csv()
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Ambassador')], 'Jane Smith')
        self.assertEqual(data[header.index('Event Manager')], 'Bob Jones')
        self.assertEqual(data[header.index('QR Codes Scanned')], '5')
        self.assertEqual(data[header.index('Recap Note')], 'Great event')

    def test_blank_ambassador_and_manager_when_unassigned(self):
        Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_type=Event.EventType.ADMIN,
            status=Event.Status.COMPLETE,
            ambassador=None,
            event_manager=None,
            date=date(2026, 5, 2),
        )
        rows = self._get_csv()
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Ambassador')], '')
        self.assertEqual(data[header.index('Event Manager')], '')
        self.assertEqual(data[header.index('QR Codes Scanned')], '')
        self.assertEqual(data[header.index('Recap Note')], '')

    def test_special_event_recap_note_uses_recap_comment(self):
        Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_type=Event.EventType.SPECIAL_EVENT,
            status=Event.Status.COMPLETE,
            account=self.account,
            date=date(2026, 5, 3),
            recap_comment="Festival was great",
        )
        rows = self._get_csv()
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Recap Note')], 'Festival was great')

# ---------------------------------------------------------------------------
# CSV expense columns
# ---------------------------------------------------------------------------

class CsvExpenseColumnsTest(TestCase):
    """
    Total Expenses and Expense Notes CSV columns appear between QR Codes Scanned
    and the per-item columns. Blank when no expenses recorded.
    """

    def setUp(self):
        self.company = make_company()
        self.admin   = make_user(self.company, 'supplier_admin', "admin")
        self.account = make_account(self.company)
        self.client  = Client()
        self.client.login(username="admin", password="testpass123")

    def _get_csv(self):
        import csv, io
        resp = self.client.get(reverse("event_export_csv"))
        reader = csv.reader(io.StringIO(resp.content.decode()))
        return list(reader)

    def _make_event(self):
        return Event.objects.create(
            company=self.company, created_by=self.admin,
            event_type=Event.EventType.TASTING,
            status=Event.Status.COMPLETE,
            account=self.account, date=date(2026, 6, 1),
        )

    def test_expense_columns_in_header(self):
        rows = self._get_csv()
        header = rows[0]
        self.assertIn('Total Expenses', header)
        self.assertIn('Expense Notes', header)

    def test_expense_columns_position(self):
        rows = self._get_csv()
        header = rows[0]
        qr_idx    = header.index('QR Codes Scanned')
        total_idx = header.index('Total Expenses')
        notes_idx = header.index('Expense Notes')
        self.assertEqual(total_idx, qr_idx + 1)
        self.assertEqual(notes_idx, qr_idx + 2)

    def test_blank_expenses_when_none(self):
        self._make_event()
        rows = self._get_csv()
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Total Expenses')], '')
        self.assertEqual(data[header.index('Expense Notes')], '')

    def test_expense_totals_and_notes(self):
        event = self._make_event()
        Expense.objects.create(
            event=event, amount='10.00', description='Parking',
            receipt_photo_url='/media/events/test/r1.jpg', created_by=self.admin,
        )
        Expense.objects.create(
            event=event, amount='5.50', description='Supplies',
            receipt_photo_url='/media/events/test/r2.jpg', created_by=self.admin,
        )
        rows = self._get_csv()
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Total Expenses')], '15.50')
        self.assertEqual(data[header.index('Expense Notes')], 'Parking | Supplies')


# ---------------------------------------------------------------------------
# Revert Recap Submitted → Scheduled (destructive)
# ---------------------------------------------------------------------------

class RevertRecapSubmittedTest(TestCase):
    """
    POST /events/<pk>/revert-recap-submitted/ should:
    - Allow: Supplier Admin, Sales Manager, assigned Event Manager
    - Block: all other users
    - Delete EventItemRecap and EventPhoto records
    - Clear recap fields on the Event
    - Revert status to Scheduled
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.sales = make_user(self.company, 'sales_manager', "sales")
        self.amb_mgr = make_user(self.company, 'ambassador_manager', "ambmgr")
        self.amb = make_user(self.company, 'ambassador', "amb")
        self.account = make_account(self.company)
        self.item = make_item(self.company)
        self.distributor = make_distributor(self.company)
        UserCoverageArea.objects.create(
            user=self.sales, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )

    def _make_recap_submitted(self, event_manager=None):
        em = event_manager or self.admin
        event = Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_manager=em,
            event_type=Event.EventType.TASTING,
            status=Event.Status.RECAP_SUBMITTED,
            ambassador=self.amb,
            account=self.account,
            date=date(2026, 6, 1),
            recap_samples_poured=25,
            recap_qr_codes_scanned=10,
            recap_notes="Great event",
            recap_comment="Some comment",
        )
        return event

    def _post(self, username, event):
        c = Client()
        c.login(username=username, password="testpass123")
        return c.post(reverse("event_revert_recap_submitted", args=[event.pk]))

    def test_supplier_admin_can_revert(self):
        event = self._make_recap_submitted()
        resp = self._post("admin", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_sales_manager_can_revert(self):
        event = self._make_recap_submitted()
        resp = self._post("sales", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_assigned_event_manager_can_revert(self):
        """An Ambassador Manager who is the assigned event_manager can revert."""
        event = self._make_recap_submitted(event_manager=self.amb_mgr)
        resp = self._post("ambmgr", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.SCHEDULED)

    def test_ambassador_cannot_revert(self):
        event = self._make_recap_submitted()
        resp = self._post("amb", event)
        self.assertEqual(resp.status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.RECAP_SUBMITTED)

    def test_wrong_status_rejected(self):
        """Cannot revert a non-Recap-Submitted event."""
        event = Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_manager=self.admin,
            event_type=Event.EventType.TASTING,
            status=Event.Status.COMPLETE,
            ambassador=self.amb,
            account=self.account,
            date=date(2026, 6, 1),
        )
        resp = self._post("admin", event)
        self.assertRedirects(resp, reverse("event_detail", args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.COMPLETE)  # unchanged

    def test_recap_fields_cleared(self):
        event = self._make_recap_submitted()
        self._post("admin", event)
        event.refresh_from_db()
        self.assertIsNone(event.recap_samples_poured)
        self.assertIsNone(event.recap_qr_codes_scanned)
        self.assertEqual(event.recap_notes, '')
        self.assertEqual(event.recap_comment, '')

    def test_event_item_recaps_deleted(self):
        from apps.events.models import EventItemRecap
        event = self._make_recap_submitted()
        EventItemRecap.objects.create(
            event=event, item=self.item, bottles_sold=5, shelf_price='12.99'
        )
        self.assertEqual(event.item_recaps.count(), 1)
        self._post("admin", event)
        self.assertEqual(EventItemRecap.objects.filter(event=event).count(), 0)

    def test_event_photos_db_records_deleted(self):
        from apps.events.models import EventPhoto
        event = self._make_recap_submitted()
        EventPhoto.objects.create(
            event=event, account=self.account,
            file_url='/media/events/1/test.jpg',
        )
        self.assertEqual(event.photos.count(), 1)
        self._post("admin", event)
        self.assertEqual(EventPhoto.objects.filter(event=event).count(), 0)

# ---------------------------------------------------------------------------
# CSV Duration decimal format
# ---------------------------------------------------------------------------

class CsvDurationDecimalTest(TestCase):
    """
    CSV export duration column should be decimal hours (2.5, 1.0, 0.75),
    not the UI string format (2hr 30m). Empty when duration is zero.
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def _get_duration_col(self, hours, minutes):
        Event.objects.all().delete()
        Event.objects.create(
            company=self.company,
            created_by=self.admin,
            event_type=Event.EventType.ADMIN,
            status=Event.Status.COMPLETE,
            date=date(2026, 5, 1),
            duration_hours=hours,
            duration_minutes=minutes,
        )
        import csv, io
        resp = self.client.get(reverse("event_export_csv"))
        reader = csv.reader(io.StringIO(resp.content.decode()))
        rows = list(reader)
        header = rows[0]
        return rows[1][header.index('Event Duration')]

    def test_two_hours_thirty_minutes(self):
        self.assertEqual(self._get_duration_col(2, 30), '2.5')

    def test_one_hour(self):
        self.assertEqual(self._get_duration_col(1, 0), '1.0')

    def test_forty_five_minutes(self):
        self.assertEqual(self._get_duration_col(0, 45), '0.75')

    def test_zero_duration_is_blank(self):
        self.assertEqual(self._get_duration_col(0, 0), '')


# ---------------------------------------------------------------------------
# Phase 10.4 — Expense add / delete AJAX
# ---------------------------------------------------------------------------

class ExpenseTest(TestCase):
    """
    expense_add and expense_delete AJAX endpoints.

    Uses SimpleUploadedFile as a minimal 1-pixel GIF receipt photo substitute.
    Storage.delete() is patched to a no-op so no real files are touched.
    """

    # Minimal valid GIF bytes — accepted by Django's image validation
    _GIF = (
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00'
        b'!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
        b'\x00\x00\x02\x02D\x01\x00;'
    )

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.accounts.models import UserCoverageArea

        self.company   = make_company()
        self.admin     = make_user(self.company, 'supplier_admin', "admin")
        self.ambassador = make_user(self.company, 'ambassador', "amb")
        self.account   = make_account(self.company)
        self.distributor = make_distributor(self.company)
        self.SimpleUploadedFile = SimpleUploadedFile

        # Ambassador needs coverage area so _can_recap returns True
        UserCoverageArea.objects.create(
            user=self.ambassador, company=self.company,
            coverage_type='account', account=self.account,
            distributor=self.distributor,
        )

        self.event = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            status=Event.Status.RECAP_IN_PROGRESS,
            date=date.today(), ambassador=self.ambassador, account=self.account,
        )

    def _receipt(self):
        return self.SimpleUploadedFile('receipt.gif', self._GIF, content_type='image/gif')

    def _add(self, username, data=None):
        c = Client()
        c.login(username=username, password="testpass123")
        payload = {'amount': '10.00', 'description': 'Parking', 'receipt_photo': self._receipt()}
        if data:
            payload.update(data)
        return c.post(
            reverse('expense_add', args=[self.event.pk]),
            data=payload,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _delete(self, username, expense):
        c = Client()
        c.login(username=username, password="testpass123")
        return c.post(
            reverse('expense_delete', args=[self.event.pk, expense.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_add_expense_success(self):
        resp = self._add('amb')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(Expense.objects.filter(event=self.event).count(), 1)
        expense = Expense.objects.get(event=self.event)
        self.assertEqual(str(expense.amount), '10.00')
        self.assertEqual(expense.description, 'Parking')
        self.assertTrue(expense.receipt_photo_url)

    def test_add_expense_missing_receipt_rejected(self):
        c = Client()
        c.login(username='amb', password='testpass123')
        resp = c.post(
            reverse('expense_add', args=[self.event.pk]),
            data={'amount': '10.00', 'description': 'Parking'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Receipt photo', resp.json()['error'])
        self.assertEqual(Expense.objects.filter(event=self.event).count(), 0)

    def test_add_expense_missing_amount_rejected(self):
        c = Client()
        c.login(username='amb', password='testpass123')
        resp = c.post(
            reverse('expense_add', args=[self.event.pk]),
            data={'description': 'Parking', 'receipt_photo': self._receipt()},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Amount', resp.json()['error'])

    def test_add_expense_blocked_when_recap_submitted(self):
        self.event.status = Event.Status.RECAP_SUBMITTED
        self.event.save(update_fields=['status'])
        resp = self._add('amb')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Expense.objects.filter(event=self.event).count(), 0)

    def test_add_expense_non_recap_user_denied(self):
        stranger = make_user(self.company, 'sales_manager', "stranger")
        # Sales Manager without coverage area cannot see the event (404)
        resp = self._add('stranger')
        self.assertEqual(resp.status_code, 404)

    def test_delete_expense_success(self):
        expense = Expense.objects.create(
            event=self.event, amount='5.00', description='Tip',
            receipt_photo_url='/media/events/test/receipt.gif',
            created_by=self.ambassador,
        )
        resp = self._delete('amb', expense)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())

    def test_delete_expense_blocked_when_recap_submitted(self):
        expense = Expense.objects.create(
            event=self.event, amount='5.00', description='Tip',
            receipt_photo_url='/media/events/test/receipt.gif',
            created_by=self.ambassador,
        )
        self.event.status = Event.Status.RECAP_SUBMITTED
        self.event.save(update_fields=['status'])
        resp = self._delete('amb', expense)
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())

    def test_revert_to_scheduled_also_deletes_expenses(self):
        """event_revert_recap_submitted clears Expense records too."""
        self.event.status = Event.Status.RECAP_SUBMITTED
        self.event.save(update_fields=['status'])
        Expense.objects.create(
            event=self.event, amount='5.00', description='Tip',
            receipt_photo_url='/media/events/test/receipt.gif',
            created_by=self.ambassador,
        )
        c = Client()
        c.login(username='admin', password='testpass123')
        c.post(reverse('event_revert_recap_submitted', args=[self.event.pk]))
        self.assertEqual(Expense.objects.filter(event=self.event).count(), 0)


# ---------------------------------------------------------------------------
# ajax_event_accounts — multi-word search
# ---------------------------------------------------------------------------

class AjaxEventAccountsSearchTest(TestCase):
    """
    ajax_event_accounts: multi-word query splits on whitespace and requires
    ALL terms to match at least one of name/street/city/state (AND across
    terms, OR within each term).
    """

    def setUp(self):
        self.company = make_company()
        # Supplier Admin sees all company accounts — no coverage area setup needed
        self.user = make_user(self.company, 'supplier_admin', 'sadmin')
        self.client = Client()
        self.client.login(username='sadmin', password='testpass123')
        self.url = reverse('ajax_event_accounts')

        # Three accounts with distinctive names and cities
        Account.objects.create(
            company=self.company, name='BuyRite Wine & Spirits',
            street='10 Bergen Ave', city='Kearny', state='NJ',
        )
        Account.objects.create(
            company=self.company, name='BuyRite Liquors',
            street='50 Market St', city='Newark', state='NJ',
        )
        Account.objects.create(
            company=self.company, name='Crown Wine & Spirits',
            street='200 Broad St', city='Newark', state='NJ',
        )

    def _get(self, q):
        return self.client.get(
            self.url, {'q': q},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _names(self, resp):
        return {a['name'] for a in resp.json()['accounts']}

    def test_short_query_returns_empty(self):
        resp = self._get('B')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['accounts'], [])

    def test_single_word_name_match(self):
        names = self._names(self._get('BuyRite'))
        self.assertIn('BuyRite Wine & Spirits', names)
        self.assertIn('BuyRite Liquors', names)
        self.assertNotIn('Crown Wine & Spirits', names)

    def test_single_word_city_match(self):
        names = self._names(self._get('Kearny'))
        self.assertIn('BuyRite Wine & Spirits', names)
        self.assertNotIn('BuyRite Liquors', names)
        self.assertNotIn('Crown Wine & Spirits', names)

    def test_multiword_name_and_city(self):
        """'BuyRite Kearny' must match only the account whose name contains
        'BuyRite' AND whose city contains 'Kearny'."""
        names = self._names(self._get('BuyRite Kearny'))
        self.assertEqual(names, {'BuyRite Wine & Spirits'})

    def test_multiword_both_in_name(self):
        """'BuyRite Liquors' matches the account where both words appear in name."""
        names = self._names(self._get('BuyRite Liquors'))
        self.assertEqual(names, {'BuyRite Liquors'})

    def test_multiword_no_match(self):
        """A term that matches nothing yields no results."""
        names = self._names(self._get('BuyRite Springfield'))
        self.assertEqual(names, set())

    def test_multiword_city_shared_across_two_accounts(self):
        """'Crown Newark' should return only Crown (name=Crown, city=Newark)."""
        names = self._names(self._get('Crown Newark'))
        self.assertEqual(names, {'Crown Wine & Spirits'})


# ---------------------------------------------------------------------------
# Ok to Pay workflow tests
# ---------------------------------------------------------------------------

class OkToPayTransitionTest(TestCase):
    """mark-ok-to-pay and revert-ok-to-pay status transitions."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', username='sa')
        self.account = make_account(self.company)
        self.event = make_event(
            self.company, self.admin,
            Event.EventType.TASTING,
            status=Event.Status.COMPLETE,
            account=self.account,
        )
        self.client = Client()
        self.client.login(username='sa', password='testpass123')

    def test_mark_ok_to_pay_transitions_complete_to_ok_to_pay(self):
        resp = self.client.post(
            reverse('event_mark_ok_to_pay', args=[self.event.pk])
        )
        self.assertRedirects(resp, reverse('event_detail', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OK_TO_PAY)

    def test_mark_ok_to_pay_requires_post(self):
        resp = self.client.get(
            reverse('event_mark_ok_to_pay', args=[self.event.pk])
        )
        self.assertRedirects(resp, reverse('event_detail', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.COMPLETE)

    def test_mark_ok_to_pay_blocked_without_permission(self):
        """Sales Manager lacks can_mark_ok_to_pay — should get 403."""
        sm = make_user(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.post(reverse('event_mark_ok_to_pay', args=[self.event.pk]))
        self.assertEqual(resp.status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.COMPLETE)

    def test_mark_ok_to_pay_blocked_for_non_complete_event(self):
        self.event.status = Event.Status.SCHEDULED
        self.event.save()
        self.client.post(reverse('event_mark_ok_to_pay', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.SCHEDULED)

    def test_revert_ok_to_pay_transitions_back_to_complete(self):
        self.event.status = Event.Status.OK_TO_PAY
        self.event.save()
        resp = self.client.post(
            reverse('event_revert_ok_to_pay', args=[self.event.pk])
        )
        self.assertRedirects(resp, reverse('event_detail', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.COMPLETE)

    def test_revert_ok_to_pay_blocked_without_permission(self):
        self.event.status = Event.Status.OK_TO_PAY
        self.event.save()
        sm = make_user(self.company, 'sales_manager', username='sm2')
        c = Client()
        c.login(username='sm2', password='testpass123')
        resp = c.post(reverse('event_revert_ok_to_pay', args=[self.event.pk]))
        self.assertEqual(resp.status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OK_TO_PAY)

    def test_payroll_reviewer_can_mark_ok_to_pay(self):
        from apps.accounts.models import UserCoverageArea
        pr = make_user(self.company, 'payroll_reviewer', username='pr')
        distributor = make_distributor(self.company)
        UserCoverageArea.objects.create(
            user=pr, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            account=self.account,
            distributor=distributor,
        )
        c = Client()
        c.login(username='pr', password='testpass123')
        resp = c.post(reverse('event_mark_ok_to_pay', args=[self.event.pk]))
        self.assertRedirects(resp, reverse('event_detail', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OK_TO_PAY)


# ---------------------------------------------------------------------------
# Payroll Reviewer event visibility tests
# ---------------------------------------------------------------------------

class PayrollReviewerVisibilityTest(TestCase):
    """Payroll Reviewer sees events at accounts in their coverage area."""

    def setUp(self):
        from apps.accounts.models import UserCoverageArea
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', username='sa')
        self.pr = make_user(self.company, 'payroll_reviewer', username='pr')
        self.account = make_account(self.company, 'Covered Account')
        self.other_account = make_account(self.company, 'Other Account')
        self.distributor = make_distributor(self.company)
        # Assign coverage for the payroll reviewer (account-level)
        UserCoverageArea.objects.create(
            user=self.pr, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            account=self.account,
            distributor=self.distributor,
        )
        self.client = Client()
        self.client.login(username='pr', password='testpass123')

    def _make_event(self, status, account=None):
        return make_event(
            self.company, self.admin,
            Event.EventType.TASTING,
            status=status,
            account=account or self.account,
        )

    def test_payroll_reviewer_sees_complete_events_in_coverage(self):
        event = self._make_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        pks = [e.pk for group in resp.context['event_groups'] for e in group[2]]
        self.assertIn(event.pk, pks)

    def test_payroll_reviewer_sees_draft_events_in_coverage(self):
        event = self._make_event(Event.Status.DRAFT)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        pks = [e.pk for group in resp.context['event_groups'] for e in group[2]]
        self.assertIn(event.pk, pks)

    def test_payroll_reviewer_does_not_see_events_outside_coverage(self):
        event = self._make_event(Event.Status.COMPLETE, account=self.other_account)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        pks = [e.pk for group in resp.context['event_groups'] for e in group[2]]
        self.assertNotIn(event.pk, pks)

    def test_payroll_reviewer_sees_ok_to_pay_events(self):
        event = self._make_event(Event.Status.OK_TO_PAY)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        pks = [e.pk for group in resp.context['event_groups'] for e in group[2]]
        self.assertIn(event.pk, pks)


# ---------------------------------------------------------------------------
# Event list — Active / Past tabs
# ---------------------------------------------------------------------------

class EventListTabsTest(TestCase):

    def setUp(self):
        self.company = make_company('Tabs Test Co')
        self.admin   = make_user(self.company, 'supplier_admin', username='sa_tabs')
        self.account = make_account(self.company, 'Tab Test Store')
        self.client  = Client()
        self.client.login(username='sa_tabs', password='testpass123')

    def _make_event(self, status):
        return make_event(
            self.company, self.admin,
            Event.EventType.TASTING,
            status=status,
            account=self.account,
        )

    def test_event_list_active_tab_excludes_paid(self):
        """Paid events do not appear in event_groups (active tab)."""
        paid_event    = self._make_event(Event.Status.PAID)
        active_event  = self._make_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        active_pks = [e.pk for group in resp.context['event_groups'] for e in group[2]]
        self.assertNotIn(paid_event.pk, active_pks)
        self.assertIn(active_event.pk, active_pks)

    def test_event_list_paid_count_in_context(self):
        """paid_count matches the number of paid events visible to the user."""
        self._make_event(Event.Status.PAID)
        self._make_event(Event.Status.PAID)
        self._make_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['paid_count'], 2)

    def test_event_list_active_count_in_context(self):
        """active_count matches the number of non-paid events visible to the user."""
        self._make_event(Event.Status.PAID)
        self._make_event(Event.Status.COMPLETE)
        self._make_event(Event.Status.SCHEDULED)
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_count'], 2)

    def test_event_list_default_tab_is_active(self):
        """With no ?tab param, active_tab context is 'active'."""
        resp = self.client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'active')

    def test_event_list_past_tab_param(self):
        """With ?tab=past, active_tab context is 'past'."""
        resp = self.client.get(reverse('event_list') + '?tab=past')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'past')

    def test_event_detail_return_tab_passed(self):
        """return_tab context is set from ?return_tab query param."""
        event = self._make_event(Event.Status.PAID)
        resp = self.client.get(
            reverse('event_detail', args=[event.pk]) + '?return_tab=past'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['return_tab'], 'past')

    def test_past_tab_links_use_return_tab_past(self):
        """Event links in the past-events pane hardcode return_tab=past."""
        event = self._make_event(Event.Status.PAID)
        resp = self.client.get(reverse('event_list') + '?tab=past')
        self.assertEqual(resp.status_code, 200)
        expected_url = reverse('event_detail', args=[event.pk]) + '?return_tab=past'
        self.assertContains(resp, expected_url)

    def test_event_detail_total_bottles_sold(self):
        """Event with item recaps shows correct total_bottles_sold in context."""
        from apps.events.models import EventItemRecap
        item1 = make_item(self.company, 'Red0750')
        item2 = make_item(self.company, 'Wht0750')
        event = self._make_event(Event.Status.COMPLETE)
        event.items.add(item1, item2)
        EventItemRecap.objects.create(event=event, item=item1, bottles_sold=5)
        EventItemRecap.objects.create(event=event, item=item2, bottles_sold=3)
        resp = self.client.get(reverse('event_detail', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_bottles_sold'], 8)
        self.assertTrue(resp.context['has_recap'])

    def test_event_detail_no_recap_hides_total(self):
        """Event with no item recaps has has_recap=False in context."""
        event = self._make_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse('event_detail', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['has_recap'])

    def test_event_detail_imported_flag_in_context(self):
        """Imported event has is_imported=True accessible in template context."""
        event = make_event(
            self.company, self.admin,
            Event.EventType.TASTING,
            status=Event.Status.PAID,
            account=self.account,
            is_imported=True,
        )
        resp = self.client.get(reverse('event_detail', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['event'].is_imported)

    def test_event_detail_total_expenses_in_context(self):
        """Event with expenses passes has_expenses=True and correct total_expenses."""
        from decimal import Decimal
        event = self._make_event(Event.Status.COMPLETE)
        Expense.objects.create(event=event, amount=Decimal('12.50'), description='Cups')
        Expense.objects.create(event=event, amount=Decimal('7.25'), description='Ice')
        resp = self.client.get(reverse('event_detail', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['has_expenses'])
        self.assertEqual(resp.context['total_expenses'], Decimal('19.75'))

    def test_event_detail_no_expenses_hides_section(self):
        """Event with no expenses passes has_expenses=False and total_expenses=0."""
        event = self._make_event(Event.Status.COMPLETE)
        resp = self.client.get(reverse('event_detail', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['has_expenses'])
        self.assertEqual(resp.context['total_expenses'], 0)


class CsvExportTabFilterTest(TestCase):
    """CSV export respects the ?tab parameter to filter paid vs active events."""

    def setUp(self):
        self.company = make_company('CSV Tab Co')
        self.admin   = make_user(self.company, 'supplier_admin', username='sa_csv_tab')
        self.account = make_account(self.company, 'CSV Tab Store')
        self.client  = Client()
        self.client.login(username='sa_csv_tab', password='testpass123')

    def _make_event(self, status):
        return make_event(
            self.company, self.admin,
            Event.EventType.TASTING,
            status=status,
            account=self.account,
            date=date(2026, 1, 15),
        )

    def _get_csv_rows(self, **params):
        import csv, io
        resp = self.client.get(reverse('event_export_csv'), params)
        self.assertEqual(resp.status_code, 200)
        return list(csv.reader(io.StringIO(resp.content.decode())))

    def test_export_csv_active_tab_excludes_paid(self):
        """?tab=active export omits paid events."""
        paid_event   = self._make_event(Event.Status.PAID)
        active_event = self._make_event(Event.Status.COMPLETE)
        rows = self._get_csv_rows(tab='active')
        pks_in_csv = {row[2] for row in rows[1:]}  # Event Date column used as proxy
        # Verify only 1 data row (active event) — simplest: count data rows
        self.assertEqual(len(rows) - 1, 1)

    def test_export_csv_past_tab_includes_only_paid(self):
        """?tab=past export includes only paid events."""
        paid_event   = self._make_event(Event.Status.PAID)
        active_event = self._make_event(Event.Status.COMPLETE)
        rows = self._get_csv_rows(tab='past')
        self.assertEqual(len(rows) - 1, 1)
        # The one data row should be the paid event
        header = rows[0]
        data = rows[1]
        self.assertEqual(data[header.index('Event Status')], Event.Status.PAID.label)


# ---------------------------------------------------------------------------
# County filter tests
# ---------------------------------------------------------------------------

def make_account_with_county(company, name, county, distributor=None):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        city='Testville',
        state='NJ',
        county=county,
        is_active=True,
    )


class CountyFilterTest(TestCase):
    """County filter on the event list — single, OR logic, admin events, session."""

    def setUp(self):
        self.company = make_company('County Filter Co')
        self.manager = make_user(self.company, 'supplier_admin', username='cf_manager')
        self.client = Client()
        self.client.login(username='cf_manager', password='testpass123')

        self.acc_essex  = make_account_with_county(self.company, 'Essex Store',  'Essex')
        self.acc_mercer = make_account_with_county(self.company, 'Mercer Store', 'Mercer')
        self.acc_hudson = make_account_with_county(self.company, 'Hudson Store', 'Hudson')

        self.evt_essex = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_essex,
            date=date(2025, 6, 1),
        )
        self.evt_mercer = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_mercer,
            date=date(2025, 6, 2),
        )
        self.evt_hudson = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_hudson,
            date=date(2025, 6, 3),
        )
        self.evt_admin = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.SCHEDULED,
            account=None,
            date=date(2025, 6, 4),
        )

    def _all_event_ids(self, response):
        """Collect all event PKs from active event_groups in the response context."""
        event_ids = set()
        for _label, _slug, events in response.context['event_groups']:
            for e in events:
                event_ids.add(e.pk)
        return event_ids

    def test_county_filter_single(self):
        """Filtering by one county returns only events for accounts in that county."""
        response = self.client.get(
            reverse('event_list'),
            {'county': 'Essex'},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._all_event_ids(response)
        self.assertIn(self.evt_essex.pk, ids)
        self.assertNotIn(self.evt_mercer.pk, ids)
        self.assertNotIn(self.evt_hudson.pk, ids)
        self.assertNotIn(self.evt_admin.pk, ids)

    def test_county_filter_or_logic(self):
        """Filtering by two counties returns events for accounts in either county."""
        response = self.client.get(
            reverse('event_list'),
            {'county': ['Essex', 'Mercer']},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._all_event_ids(response)
        self.assertIn(self.evt_essex.pk, ids)
        self.assertIn(self.evt_mercer.pk, ids)
        self.assertNotIn(self.evt_hudson.pk, ids)
        self.assertNotIn(self.evt_admin.pk, ids)

    def test_county_filter_excludes_admin_events(self):
        """Admin events (account=None) are excluded when a county filter is active."""
        response = self.client.get(
            reverse('event_list'),
            {'county': 'Essex'},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._all_event_ids(response)
        self.assertNotIn(self.evt_admin.pk, ids)

    def test_county_filter_persisted_in_session(self):
        """County filter is saved to and restored from session."""
        self.client.get(
            reverse('event_list'),
            {'county': ['Essex', 'Mercer']},
        )
        session = self.client.session
        saved = session.get('event_list_filters')
        self.assertIsNotNone(saved, 'Filters were not saved to session')
        self.assertIn('Essex', saved['county'])
        self.assertIn('Mercer', saved['county'])


# ---------------------------------------------------------------------------
# City filter multi-select tests
# ---------------------------------------------------------------------------

def make_account_with_city(company, name, city, county='Unknown', distributor=None):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        city=city,
        state='NJ',
        county=county,
        is_active=True,
    )


class CityFilterMultiSelectTest(TestCase):
    """City filter on the event list — multi-select OR logic."""

    def setUp(self):
        self.company = make_company('City Filter Co')
        self.manager = make_user(self.company, 'supplier_admin', username='city_manager')
        self.client = Client()
        self.client.login(username='city_manager', password='testpass123')

        self.acc_hoboken = make_account_with_city(self.company, 'Hoboken Store', 'Hoboken')
        self.acc_newark  = make_account_with_city(self.company, 'Newark Store',  'Newark')
        self.acc_trenton = make_account_with_city(self.company, 'Trenton Store', 'Trenton')

        self.evt_hoboken = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_hoboken,
            date=date(2025, 7, 1),
        )
        self.evt_newark = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_newark,
            date=date(2025, 7, 2),
        )
        self.evt_trenton = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_trenton,
            date=date(2025, 7, 3),
        )

    def _active_event_ids(self, response):
        event_ids = set()
        for _label, _slug, events in response.context['event_groups']:
            for e in events:
                event_ids.add(e.pk)
        return event_ids

    def test_city_filter_multi_select(self):
        """Filtering by multiple cities returns events for accounts in any of those cities."""
        response = self.client.get(
            reverse('event_list'),
            {'city': ['Hoboken', 'Newark']},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._active_event_ids(response)
        self.assertIn(self.evt_hoboken.pk, ids)
        self.assertIn(self.evt_newark.pk, ids)
        self.assertNotIn(self.evt_trenton.pk, ids)

    def test_city_filter_single(self):
        """Filtering by a single city returns only events in that city."""
        response = self.client.get(
            reverse('event_list'),
            {'city': 'Hoboken'},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._active_event_ids(response)
        self.assertIn(self.evt_hoboken.pk, ids)
        self.assertNotIn(self.evt_newark.pk, ids)
        self.assertNotIn(self.evt_trenton.pk, ids)

    def test_city_filter_csv_respected(self):
        """CSV export with city filter in session returns only matching events."""
        # First save city filter to session
        self.client.get(reverse('event_list'), {'city': ['Hoboken']})

        response = self.client.get(reverse('event_export_csv'), {'tab': 'active'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Hoboken Store', content)
        self.assertNotIn('Newark Store', content)
        self.assertNotIn('Trenton Store', content)

    def test_city_options_reflect_distributor_filter(self):
        """After filtering by distributor, available_cities only contains cities
        from that distributor's events."""
        dist_a = make_distributor(self.company, name='Dist A')
        dist_b = make_distributor(self.company, name='Dist B')

        acc_dist_a = make_account_with_city(
            self.company, 'Dist A Store', 'Bayonne', distributor=dist_a
        )
        acc_dist_b = make_account_with_city(
            self.company, 'Dist B Store', 'Princeton', distributor=dist_b
        )
        make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=acc_dist_a,
            date=date(2025, 7, 10),
        )
        make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=acc_dist_b,
            date=date(2025, 7, 11),
        )

        response = self.client.get(
            reverse('event_list'),
            {'distributor': str(dist_a.pk)},
        )
        self.assertEqual(response.status_code, 200)
        available_cities = response.context['available_cities']
        self.assertIn('Bayonne', available_cities)
        self.assertNotIn('Princeton', available_cities)


# ---------------------------------------------------------------------------
# County filter CSV export test
# ---------------------------------------------------------------------------

class CountyFilterCsvTest(TestCase):
    """CSV export respects county filter from session."""

    def setUp(self):
        self.company = make_company('County CSV Co')
        self.manager = make_user(self.company, 'supplier_admin', username='cc_manager')
        self.client = Client()
        self.client.login(username='cc_manager', password='testpass123')

        self.acc_essex  = make_account_with_county(self.company, 'Essex CSV Store',  'Essex')
        self.acc_mercer = make_account_with_county(self.company, 'Mercer CSV Store', 'Mercer')

        make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_essex,
            date=date(2025, 8, 1),
        )
        make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_mercer,
            date=date(2025, 8, 2),
        )

    def test_county_filter_csv_respected(self):
        """CSV export with county filter in session returns only matching events."""
        # Save county filter to session
        self.client.get(reverse('event_list'), {'county': ['Essex']})

        response = self.client.get(reverse('event_export_csv'), {'tab': 'active'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Essex CSV Store', content)
        self.assertNotIn('Mercer CSV Store', content)


# ---------------------------------------------------------------------------
# Shared filter function unit tests
# ---------------------------------------------------------------------------

class SharedFilterFunctionTest(TestCase):
    """get_filtered_event_queryset applies all filters correctly."""

    def setUp(self):
        from apps.events.views import get_filtered_event_queryset
        self.get_filtered_event_queryset = get_filtered_event_queryset

        self.company = make_company('Filter Fn Co')
        self.manager = make_user(self.company, 'supplier_admin', username='ff_manager')

        self.acc_hoboken = make_account_with_city(self.company, 'HK Store', 'Hoboken', county='Hudson')
        self.acc_newark  = make_account_with_city(self.company, 'NK Store', 'Newark',  county='Essex')

        self.evt_hoboken = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_hoboken,
            date=date(2025, 9, 1),
        )
        self.evt_newark = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc_newark,
            date=date(2025, 9, 2),
        )

    def _base_qs(self):
        return Event.objects.filter(company=self.company).select_related('account')

    def test_city_filter_list(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'city': ['Hoboken']})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_hoboken.pk, pks)
        self.assertNotIn(self.evt_newark.pk, pks)

    def test_city_filter_string_backward_compat(self):
        """A string city value is treated as a single-item list."""
        qs = self.get_filtered_event_queryset(self._base_qs(), {'city': 'Hoboken'})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_hoboken.pk, pks)
        self.assertNotIn(self.evt_newark.pk, pks)

    def test_county_filter_list(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'county': ['Essex']})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_newark.pk, pks)
        self.assertNotIn(self.evt_hoboken.pk, pks)

    def test_county_filter_or_logic(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'county': ['Essex', 'Hudson']})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_hoboken.pk, pks)
        self.assertIn(self.evt_newark.pk, pks)

    def test_year_filter(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'year': '2025'})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_hoboken.pk, pks)
        self.assertIn(self.evt_newark.pk, pks)

    def test_year_filter_no_match(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'year': '2020'})
        self.assertEqual(qs.count(), 0)

    def test_account_name_filter(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {'account_name': 'HK'})
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(self.evt_hoboken.pk, pks)
        self.assertNotIn(self.evt_newark.pk, pks)

    def test_empty_filters_returns_all(self):
        qs = self.get_filtered_event_queryset(self._base_qs(), {})
        self.assertEqual(qs.count(), 2)


# ---------------------------------------------------------------------------
# Filter modal — multi-value field tests
# ---------------------------------------------------------------------------

class FilterModalMultiValueTest(TestCase):
    """Multi-value checkbox filters: event_type, year, and active_filter_count."""

    def setUp(self):
        self.company = make_company('Modal Filter Co')
        self.manager = make_user(self.company, 'supplier_admin', username='modal_mgr')
        self.client = Client()
        self.client.login(username='modal_mgr', password='testpass123')

        self.acc = make_account(self.company, name='Modal Store')

        self.evt_tasting = make_event(
            self.company, self.manager, Event.EventType.TASTING,
            status=Event.Status.SCHEDULED,
            account=self.acc,
            date=date(2024, 3, 1),
        )
        self.evt_special = make_event(
            self.company, self.manager, Event.EventType.SPECIAL_EVENT,
            status=Event.Status.SCHEDULED,
            account=self.acc,
            date=date(2025, 3, 1),
        )
        self.evt_admin = make_event(
            self.company, self.manager, Event.EventType.ADMIN,
            status=Event.Status.SCHEDULED,
            account=None,
            date=date(2025, 4, 1),
        )

    def _active_event_ids(self, response):
        event_ids = set()
        for _label, _slug, events in response.context['event_groups']:
            for e in events:
                event_ids.add(e.pk)
        return event_ids

    def test_filter_modal_multi_type(self):
        """Filtering by multiple event types returns events of any matching type."""
        response = self.client.get(
            reverse('event_list'),
            {'event_type': ['tasting', 'special_event']},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._active_event_ids(response)
        self.assertIn(self.evt_tasting.pk, ids)
        self.assertIn(self.evt_special.pk, ids)
        self.assertNotIn(self.evt_admin.pk, ids)

    def test_filter_modal_multi_year(self):
        """Filtering by multiple years returns events from any of those years."""
        response = self.client.get(
            reverse('event_list'),
            {'year': ['2024', '2025']},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._active_event_ids(response)
        self.assertIn(self.evt_tasting.pk, ids)
        self.assertIn(self.evt_special.pk, ids)
        self.assertIn(self.evt_admin.pk, ids)

    def test_filter_modal_single_year(self):
        """Filtering by a single year excludes events from other years."""
        response = self.client.get(
            reverse('event_list'),
            {'year': ['2024']},
        )
        self.assertEqual(response.status_code, 200)
        ids = self._active_event_ids(response)
        self.assertIn(self.evt_tasting.pk, ids)
        self.assertNotIn(self.evt_special.pk, ids)
        self.assertNotIn(self.evt_admin.pk, ids)

    def test_active_filter_count_in_context(self):
        """active_filter_count matches the number of filter groups that are active."""
        # Two filter groups active: event_type and year
        response = self.client.get(
            reverse('event_list'),
            {'event_type': ['tasting'], 'year': ['2024', '2025']},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_filter_count'], 2)

    def test_active_filter_count_zero_when_no_filters(self):
        """active_filter_count is 0 when no filters are active."""
        # Clear any session filters first
        self.client.get(reverse('event_list') + '?clear_filters=1')
        response = self.client.get(reverse('event_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_filter_count'], 0)


# ---------------------------------------------------------------------------
# BUG 1 — Event manager can be changed on edit
# ---------------------------------------------------------------------------

class EventManagerEditTest(TestCase):
    """
    Changing event_manager on the edit form correctly saves the new value.

    Regression: on edit, the em queryset was restricted to role-eligible users
    and didn't include the currently-assigned manager, causing form validation
    to reject any submitted event_manager value.
    """

    def setUp(self):
        self.company   = make_company()
        self.admin     = make_user(self.company, 'supplier_admin', 'admin')
        self.mgr_a     = make_user(self.company, 'ambassador_manager', 'mgr_a')
        self.mgr_b     = make_user(self.company, 'ambassador_manager', 'mgr_b')
        self.account   = make_account(self.company)
        self.event     = make_event(
            self.company, self.admin, Event.EventType.TASTING,
            account=self.account,
            date=date.today(),
        )
        # Set mgr_a as the current event manager (make_event defaults to creator)
        self.event.event_manager = self.mgr_a
        self.event.save(update_fields=['event_manager'])
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_event_manager_can_be_changed_on_edit(self):
        url = reverse('event_edit', args=[self.event.pk])
        response = self.client.post(url, {
            'event_type': 'tasting',
            'account':    self.account.pk,
            'date':       str(date.today()),
            'start_time': '13:00',
            'duration_hours':   '2',
            'duration_minutes': '0',
            'ambassador':     '',
            'event_manager':  self.mgr_b.pk,
            'notes':          '',
        })
        self.assertRedirects(response, reverse('event_detail', args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.event_manager, self.mgr_b)


# ---------------------------------------------------------------------------
# BUG 4 — Tasting / Special Event requires account
# ---------------------------------------------------------------------------

class TastingEventRequiresAccountTest(TestCase):
    """
    EventForm must reject Tasting and Special Event submissions with no account.
    """

    def setUp(self):
        self.company = make_company()
        self.admin   = make_user(self.company, 'supplier_admin', 'admin')
        self.client  = Client()
        self.client.login(username='admin', password='testpass123')

    def _post_create(self, event_type, account_pk=''):
        url = reverse('event_create') + f'?event_type={event_type}'
        return self.client.post(url, {
            'event_type':       event_type,
            'account':          account_pk,
            'date':             str(date.today()),
            'start_time':       '13:00',
            'duration_hours':   '2',
            'duration_minutes': '0',
            'ambassador':       '',
            'event_manager':    '',
            'notes':            '',
        })

    def test_tasting_without_account_is_invalid(self):
        response = self._post_create('tasting', account_pk='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'Please select an account')

    def test_special_event_without_account_is_invalid(self):
        response = self._post_create('special_event', account_pk='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(response, 'Please select an account')

    def test_tasting_with_account_is_valid(self):
        account = make_account(self.company)
        response = self._post_create('tasting', account_pk=account.pk)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.first().account, account)

    def test_admin_event_without_account_is_valid(self):
        response = self._post_create('admin', account_pk='')
        self.assertEqual(Event.objects.count(), 1)
