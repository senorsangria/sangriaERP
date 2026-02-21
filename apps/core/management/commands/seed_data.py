"""
Management command: seed_data

Populates the database with baseline test data:
  - Company: Drink Up Life, Inc
  - Brand: Señor Sangria  (7 SKUs)
  - Brand: Backyard Barrel Co  (2 placeholder SKUs)

Safe to run multiple times — uses get_or_create throughout.

Usage:
    python manage.py seed_data
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Seed the database with baseline test data for productERP.'

    def handle(self, *args, **options):
        self.stdout.write('Seeding productERP test data...')

        with transaction.atomic():
            self._seed_company()

        self.stdout.write(self.style.SUCCESS('Seed complete.'))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _seed_company(self):
        from apps.core.models import Company
        from apps.catalog.models import Brand, Item

        # ---- Company -------------------------------------------------
        company, created = Company.objects.get_or_create(
            slug='drink-up-life',
            defaults={'name': 'Drink Up Life, Inc', 'is_active': True},
        )
        action = 'Created' if created else 'Found'
        self.stdout.write(f'  {action} company: {company.name}')

        # ---- Brand: Señor Sangria ------------------------------------
        senor, created = Brand.objects.get_or_create(
            company=company,
            name='Señor Sangria',
            defaults={'description': 'Premium sangria wines.', 'is_active': True},
        )
        self.stdout.write(f'  {"Created" if created else "Found"} brand: {senor.name}')

        senor_skus = [
            # (item_code, name)
            ('Red0750',      'Classic Red 750ml'),
            ('Red1500',      'Classic Red 1.5L'),
            ('Wht0750',      'Classic White 750ml'),
            ('Wht1500',      'Classic White 1.5L'),
            ('SpkRed0750',   'Spiked Red 750ml'),
            ('SprRed12oz',   'Spritz Red 12oz'),
            ('SprWhite12oz', 'Spritz White 12oz'),
        ]

        for code, name in senor_skus:
            item, created = Item.objects.get_or_create(
                brand=senor,
                item_code=code,
                defaults={'name': name, 'sku_number': '', 'is_active': True},
            )
            self.stdout.write(f'    {"Created" if created else "Found"} item: {item.item_code} — {item.name}')

        # ---- Brand: Backyard Barrel Co ------------------------------
        backyard, created = Brand.objects.get_or_create(
            company=company,
            name='Backyard Barrel Co',
            defaults={'description': 'Small-batch barrel-aged spirits.', 'is_active': True},
        )
        self.stdout.write(f'  {"Created" if created else "Found"} brand: {backyard.name}')

        backyard_skus = [
            ('BBCWhiskey0750', 'Backyard Whiskey 750ml'),
            ('BBCBourbon0750', 'Backyard Bourbon 750ml'),
        ]

        for code, name in backyard_skus:
            item, created = Item.objects.get_or_create(
                brand=backyard,
                item_code=code,
                defaults={'name': name, 'sku_number': '', 'is_active': True},
            )
            self.stdout.write(f'    {"Created" if created else "Found"} item: {item.item_code} — {item.name}')
