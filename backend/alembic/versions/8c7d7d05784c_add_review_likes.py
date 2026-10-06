"""add review likes"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c7d7d05784c'
down_revision: Union[str, Sequence[str], None] = '2de200ec4855'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'review_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('review_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('review_id', 'user_id', name='uq_review_user_like'),
    )
    op.create_index(op.f('ix_review_likes_review_id'), 'review_likes', ['review_id'], unique=False)
    op.create_index(op.f('ix_review_likes_user_id'), 'review_likes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_review_likes_user_id'), table_name='review_likes')
    op.drop_index(op.f('ix_review_likes_review_id'), table_name='review_likes')
    op.drop_table('review_likes')
