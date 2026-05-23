"""add role permissions

Revision ID: 1a2b3c4d5e6f
Revises: 0f9a2b3c4d5e
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa


revision = "1a2b3c4d5e6f"
down_revision = "0f9a2b3c4d5e"
branch_labels = None
depends_on = None


DEFAULT_ROLE_PERMISSIONS = {
    "admin": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "edit",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "edit",
    },
    "methodist": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "metodist_editor": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "operator": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "tpmpk_admin": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "tpmpk_operator": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "domu_editor": {
        "articles": "edit",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "user": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "none",
        "portal_settings": "none",
    },
}


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    return any(column["name"] == column_name for column in sa.inspect(bind).get_columns(table_name))


def upgrade() -> None:
    if not _table_exists("user_role"):
        return

    bind = op.get_bind()
    dialect = bind.dialect.name
    if not _column_exists("user_role", "permissions"):
        server_default = sa.text("'{}'::jsonb") if dialect == "postgresql" else sa.text("'{}'")
        op.add_column(
            "user_role",
            sa.Column("permissions", sa.JSON(), nullable=False, server_default=server_default),
        )

    user_role = sa.table(
        "user_role",
        sa.column("role_name", sa.String),
        sa.column("permissions", sa.JSON()),
    )
    for role_name, permissions in DEFAULT_ROLE_PERMISSIONS.items():
        bind.execute(
            user_role.update()
            .where(user_role.c.role_name == role_name)
            .values(permissions=permissions)
        )


def downgrade() -> None:
    if _table_exists("user_role") and _column_exists("user_role", "permissions"):
        op.drop_column("user_role", "permissions")
