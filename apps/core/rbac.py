"""
RBAC models: Permission and Role.

These are the two new building blocks for the three-layer permission system:
  User → roles (M2M) → Role → permissions (M2M) → Permission

Phase 10.5
"""
from django.db import models


class Permission(models.Model):
    """
    A single granular permission.

    codename is the string used in code, e.g. 'can_release_event'.
    description is a human-readable explanation shown in the admin UI.
    """

    codename = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255)

    class Meta:
        ordering = ['codename']

    def __str__(self):
        return self.codename


class Role(models.Model):
    """
    A named role that bundles a set of permissions.

    name     — display name, e.g. 'Supplier Admin'
    codename — slug used in code, e.g. 'supplier_admin'
    permissions — M2M to Permission; the set of things this role can do
    """

    name = models.CharField(max_length=100, unique=True)
    codename = models.CharField(max_length=100, unique=True)
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name='roles',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
