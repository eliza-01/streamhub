"""public site catalog and explicit event publishing

Revision ID: 0010_site_catalog
Revises: 0009_telegram_storage
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0010_site_catalog"
down_revision = "0009_telegram_storage"
branch_labels = None
depends_on = None

_DEFAULT_CATEGORIES = (
    ("films", "Фильмы", 10),
    ("shows", "Шоу", 20),
    ("games", "Игры", 30),
    ("fncs", "FNCS", 40),
    ("chatroulette", "Чатрулетка", 50),
)


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("content_categories"):
        op.create_table(
            "content_categories",
            sa.Column("id", mysql.SMALLINT(unsigned=True), autoincrement=True, nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("label_ru", sa.String(length=64), nullable=False),
            sa.Column("sort_order", mysql.SMALLINT(unsigned=True), nullable=False, server_default="100"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", name="uq_content_categories_slug"),
        )
        op.create_index("ix_content_categories_active_sort", "content_categories", ["is_active", "sort_order"])

    bind = op.get_bind()
    for slug, label, sort_order in _DEFAULT_CATEGORIES:
        bind.execute(
            sa.text(
                "INSERT INTO content_categories (slug,label_ru,sort_order,is_active) "
                "VALUES (:slug,:label,:sort_order,1) "
                "ON DUPLICATE KEY UPDATE label_ru=VALUES(label_ru),sort_order=VALUES(sort_order),is_active=1"
            ),
            {"slug": slug, "label": label, "sort_order": sort_order},
        )

    if not _table_exists("site_event_publications"):
        op.create_table(
            "site_event_publications",
            sa.Column("event_id", sa.BINARY(16), nullable=False),
            sa.Column("published_at_utc", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("event_id"),
        )
        op.create_index("ix_site_event_publications_published", "site_event_publications", ["published_at_utc"])

    if not _table_exists("event_categories"):
        op.create_table(
            "event_categories",
            sa.Column("event_id", sa.BINARY(16), nullable=False),
            sa.Column("category_id", mysql.SMALLINT(unsigned=True), nullable=False),
            sa.Column("position", mysql.TINYINT(unsigned=True), nullable=False, server_default="1"),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
            sa.ForeignKeyConstraint(["event_id"], ["media_events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["category_id"], ["content_categories.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("event_id", "category_id"),
            sa.UniqueConstraint("event_id", "position", name="uq_event_categories_position"),
        )
        op.create_index("ix_event_categories_category_event", "event_categories", ["category_id", "event_id"])


def downgrade() -> None:
    op.drop_table("event_categories")
    op.drop_table("site_event_publications")
    op.drop_table("content_categories")
