"""review ownership + movie verification

Adds ``reviews.movie_id`` (temporarily nullable), deterministically backfills
it from each reviewer's confirmed bookings, then enforces the final
invariant: ``movie_id`` is NOT NULL, every review points at exactly one movie
via an FK to ``movies``, protected by ``uq_review_user_movie``.

Failure policy: after the deterministic backfill, any review still without a
movie aborts the migration with the offending row ids instead of guessing an
association or silently violating NOT NULL. Every step is guarded so the
failed run can simply be re-run after the operator resolves the rows —
SQLite DDL is not transactional, so re-runnability is part of the design.

Dialect strategy: SQLite cannot ``ALTER COLUMN ... SET NOT NULL``, so every
column/constraint change goes through Alembic's ``batch_alter_table``, which
recreates the table on SQLite (copying rows) and compiles to plain
``ALTER TABLE`` on PostgreSQL. One batch pass performs the NOT NULL flip, the
new column and all constraints/indexes so the table is recreated exactly
once with the constraints named in the generated DDL.
"""
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

    inspector = sa.inspect(conn)
    review_cols = {c['name']: c for c in inspector.get_columns('reviews')}

    # 1. Additive column; nullable while the backfill lands so existing rows
    #    stay valid throughout the migration. Guarded so a re-run after a
    #    reported failure does not trip over a duplicate column.
    if 'movie_id' not in review_cols:
        with op.batch_alter_table('reviews', schema=None) as batch_op:
            batch_op.add_column(sa.Column('movie_id', sa.Integer(), nullable=True))

    # 2. Deterministic backfill. A legacy review may inherit a movie only when
    #    every one of its author's confirmed bookings points to that same
    #    movie. No booking, or several movies, means the review stays unmapped
    #    — the migration never guesses an association.
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

        if len(movies) == 1 and movies[0][0] is not None:
            conn.execute(
                sa.text(
                    'UPDATE reviews SET movie_id = :movie_id WHERE id = :review_id'
                ),
                {'movie_id': movies[0][0], 'review_id': review_id},
            )

    # 3. Detect any review the backfill could not resolve and FAIL loudly:
    #    NOT NULL (step 4) would otherwise abort with a generic constraint
    #    error, and guessing here would corrupt the review→movie relationship.
    #    The column exists at this point, so the fix is directly actionable
    #    (UPDATE the listed rows to the correct movie, or delete them) and the
    #    migration can then simply be re-run.
    still_null = conn.execute(
        sa.text('SELECT id FROM reviews WHERE movie_id IS NULL')
    ).fetchall()
    if still_null:
        sample = ', '.join(str(row[0]) for row in still_null[:20])
        more = '…' if len(still_null) > 20 else ''
        raise RuntimeError(
            f'{len(still_null)} legacy review(s) have no single confirmed-'
            'booking movie to backfill, so NOT NULL on reviews.movie_id '
            f'cannot be enforced (review ids: {sample}{more}). Refusing to '
            'guess an association. UPDATE these rows to the correct movie_id '
            '(or delete them) and re-run the migration.'
        )

    # 4. Data is settled — enforce NOT NULL, add ``updated_at`` and create the
    #    FK / unique constraint / indexes in a single batch pass (exactly one
    #    table recreation on SQLite; plain ALTER/CREATE on PostgreSQL). Keeping
    #    it to one pass means the constraints are named directly in the
    #    generated DDL instead of being round-tripped through reflection. Every
    #    operation is conditional so a re-run after a reported failure only
    #    performs what is still missing.
    inspector = sa.inspect(conn)
    review_cols = {c['name']: c for c in inspector.get_columns('reviews')}
    unique_names = {c['name'] for c in inspector.get_unique_constraints('reviews')}
    index_names = {c['name'] for c in inspector.get_indexes('reviews')}
    fk_targets = {
        (fk['referred_table'], tuple(fk['constrained_columns']))
        for fk in inspector.get_foreign_keys('reviews')
    }

    need_not_null = 'movie_id' in review_cols and review_cols['movie_id']['nullable']
    need_updated_at = 'updated_at' not in review_cols
    need_ix_movie = 'ix_reviews_movie_id' not in index_names
    need_ix_composite = 'ix_reviews_movie_created_at' not in index_names
    need_unique = 'uq_review_user_movie' not in unique_names
    need_fk = ('movies', ('movie_id',)) not in fk_targets

    if any((need_not_null, need_updated_at, need_ix_movie, need_ix_composite,
            need_unique, need_fk)):
        with op.batch_alter_table('reviews', schema=None) as batch_op:
            if need_not_null:
                batch_op.alter_column(
                    'movie_id',
                    existing_type=sa.Integer(),
                    existing_nullable=True,
                    nullable=False,
                )
            if need_updated_at:
                batch_op.add_column(
                    sa.Column(
                        'updated_at',
                        sa.DateTime(),
                        nullable=True,
                        server_default=sa.text('(CURRENT_TIMESTAMP)'),
                    )
                )
            if need_ix_movie:
                batch_op.create_index(batch_op.f('ix_reviews_movie_id'), ['movie_id'], unique=False)
            if need_ix_composite:
                batch_op.create_index(batch_op.f('ix_reviews_movie_created_at'), ['movie_id', 'created_at'], unique=False)
            if need_unique:
                batch_op.create_unique_constraint('uq_review_user_movie', ['user_id', 'movie_id'])
            if need_fk:
                batch_op.create_foreign_key(
                    'fk_reviews_movie_id_movies',
                    'movies',
                    ['movie_id'],
                    ['id'],
                    ondelete='CASCADE',
                )

    # 5. ``updated_at`` mirrors ``created_at`` for pre-existing rows (the ORM
    #    maintains it from here on via ``onupdate``); idempotent on re-run.
    conn.execute(sa.text('UPDATE reviews SET updated_at = created_at WHERE updated_at IS NULL'))


def downgrade() -> None:
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.drop_constraint('uq_review_user_movie', type_='unique')
        batch_op.drop_index(batch_op.f('ix_reviews_movie_created_at'))
        batch_op.drop_index(batch_op.f('ix_reviews_movie_id'))
        batch_op.drop_constraint('fk_reviews_movie_id_movies', type_='foreignkey')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('movie_id')
