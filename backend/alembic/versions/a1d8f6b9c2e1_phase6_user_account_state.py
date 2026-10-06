"""phase 6: user account state + token invalidation"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1d8f6b9c2e1'
down_revision: Union[str, Sequence[str], None] = 'c4a1f2b09d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('token_version', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('token_version')
        batch_op.drop_column('is_active')
