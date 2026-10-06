"""review ownership + movie verification"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b84d736f1d45'
down_revision: Union[str, Sequence[str], None] = 'a1d8f6b9c2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('movie_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_reviews_movie_id'), ['movie_id'], unique=False)
        batch_op.create_unique_constraint('uq_review_user_movie', ['user_id', 'movie_id'])
        batch_op.create_index(batch_op.f('ix_reviews_movie_created_at'), ['movie_id', 'created_at'], unique=False)
        batch_op.create_foreign_key(
            'fk_reviews_movie_id_movies',
            'movies',
            ['movie_id'],
            ['id'],
            ondelete='CASCADE',
        )

    # Backfill rows conservatively: if a review existed without movie_id, attach
    # it to the first movie from the related show, when a confirmed booking exists.
    conn = op.get_bind()
    review_rows = conn.execute(sa.text("SELECT id, user_id FROM reviews WHERE movie_id IS NULL")).fetchall()
    for row in review_rows:
        review_id, user_id = row
        movie_id = conn.execute(
            sa.text(
                "SELECT s.movie_id FROM bookings b JOIN shows s ON s.id = b.show_id "
                "WHERE b.user_id = :user_id AND b.status = 'CONFIRMED' "
                "ORDER BY b.created_at DESC LIMIT 1"
            ),
            {"user_id": user_id},
        ).scalar()
        if movie_id is not None:
            conn.execute(sa.text("UPDATE reviews SET movie_id = :movie_id WHERE id = :review_id"), {"movie_id": movie_id, "review_id": review_id})

    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.alter_column('movie_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('(CURRENT_TIMESTAMP)')))


def downgrade() -> None:
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.drop_constraint('uq_review_user_movie', type_='unique')
        batch_op.drop_index(batch_op.f('ix_reviews_movie_created_at'))
        batch_op.drop_index(batch_op.f('ix_reviews_movie_id'))
        batch_op.drop_constraint('fk_reviews_movie_id_movies', type_='foreignkey')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('movie_id')
