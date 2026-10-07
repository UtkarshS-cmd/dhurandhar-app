"""review ownership + movie verification"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b84d736f1d45'
down_revision: Union[str, Sequence[str], None] = 'a1d8f6b9c2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 0. Remove stale SQLite temp-table artifacts (*_alembic_tmp_*) left by
    #    interrupted/legacy runs so the upgrade never aborts on existing DDL.
    conn.execute(sa.text('DROP TABLE IF EXISTS _alembic_tmp_reviews'))

    # 1. Additive column; nullable so pre-existing rows stay valid.
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('movie_id', sa.Integer(), nullable=True))

    # 2. Deterministic backfill. A legacy review may inherit a movie only when
    #    every one of its author's confirmed bookings points to that same
    #    movie. No booking, or several movies, means the review stays unmapped.
    legacy_rows = conn.execute(
        sa.text('SELECT id, user_id FROM reviews WHERE movie_id IS NULL')
    ).fetchall()

    for review_id, user_id in legacy_rows:
        movies = conn.execute(
            sa.text(
                'SELECT DISTINCT s.movie_id FROM bookings b '
                'JOIN shows s ON s.id = b.show_id '
                "WHERE b.user_id = :user_id AND b.status = 'CONFIRMED'"
            ),
            {'user_id': user_id},
        ).fetchall()

        if len(movies) == 1:
            movie_id = movies[0][0]
            if movie_id is not None:
                conn.execute(
                    sa.text(
                        'UPDATE reviews SET movie_id = :movie_id WHERE id = :review_id'
                    ),
                    {'movie_id': movie_id, 'review_id': review_id},
                )

    # 3. Enforce the relationships only now that the data is settled.
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reviews_movie_id'), ['movie_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_reviews_movie_created_at'), ['movie_id', 'created_at'], unique=False)
        batch_op.create_unique_constraint('uq_review_user_movie', ['user_id', 'movie_id'])
        batch_op.create_foreign_key(
            'fk_reviews_movie_id_movies',
            'movies',
            ['movie_id'],
            ['id'],
            ondelete='CASCADE',
        )

    # 4. ``updated_at`` is required by the Review ORM model's ``onupdate``.
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'updated_at',
                sa.DateTime(),
                nullable=True,
                server_default=sa.text('(CURRENT_TIMESTAMP)'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.drop_constraint('uq_review_user_movie', type_='unique')
        batch_op.drop_index(batch_op.f('ix_reviews_movie_created_at'))
        batch_op.drop_index(batch_op.f('ix_reviews_movie_id'))
        batch_op.drop_constraint('fk_reviews_movie_id_movies', type_='foreignkey')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('movie_id')
