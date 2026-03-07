"""
Management command: create_saas_admin

Creates a SaaS Admin user interactively.

A SaaS Admin has no company affiliation and has full platform access
(can_view_saas_admin_ui and all other permissions).

Usage:
    python manage.py create_saas_admin
"""
from django.core.management.base import BaseCommand

from apps.core.models import User
from apps.core.rbac import Role


class Command(BaseCommand):
    help = 'Create a SaaS Admin user interactively.'

    def handle(self, *args, **options):
        self.stdout.write('\nCreate SaaS Admin Account')
        self.stdout.write('=' * 30)

        username = input('Username: ').strip()
        if not username:
            self.stderr.write('Username cannot be empty.')
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.WARNING(f'User "{username}" already exists.')
            )
            return

        email = input('Email (optional): ').strip()
        first_name = input('First name (optional): ').strip()
        last_name = input('Last name (optional): ').strip()

        import getpass
        password = getpass.getpass('Password: ')
        if not password:
            self.stderr.write('Password cannot be empty.')
            return

        try:
            role = Role.objects.get(codename='saas_admin')
        except Role.DoesNotExist:
            self.stderr.write(
                'saas_admin role not found. Run migrations first.'
            )
            return

        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            company=None,
            is_staff=True,
        )
        user.roles.add(role)

        self.stdout.write(
            self.style.SUCCESS(
                f'\nSaaS Admin "{username}" created successfully.'
            )
        )
