"""phase 4: payment attempts + webhook idempotency"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c4a1f2b09d33'
down_revision: Union[str, Sequence[str], None] = '8c7d7d05784c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('payment_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('booking_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('provider_order_id', sa.String(length=120), nullable=True),
    sa.Column('provider_payment_id', sa.String(length=120), nullable=True),
    sa.Column('amount', sa.Numeric(10, 2), nullable=False),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('failure_code', sa.String(length=80), nullable=True),
    sa.Column('failure_reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('booking_id', 'attempt_no', name='uq_payment_attempt_no')
    )
    op.create_index(op.f('ix_payment_attempts_booking_id'), 'payment_attempts', ['booking_id'], unique=False)
    op.create_index(op.f('ix_payment_attempts_provider'), 'payment_attempts', ['provider'], unique=False)
    op.create_index(op.f('ix_payment_attempts_provider_order_id'), 'payment_attempts', ['provider_order_id'], unique=False)
    op.create_index(op.f('ix_payment_attempts_provider_payment_id'), 'payment_attempts', ['provider_payment_id'], unique=False)
    op.create_index(op.f('ix_payment_attempts_status'), 'payment_attempts', ['status'], unique=False)
    op.create_index('ix_payment_attempt_provider_order', 'payment_attempts', ['provider', 'provider_order_id'], unique=False)
    op.create_table('payment_webhook_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('event_id', sa.String(length=160), nullable=False),
    sa.Column('event_type', sa.String(length=120), nullable=False),
    sa.Column('payment_attempt_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['payment_attempt_id'], ['payment_attempts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'event_id', name='uq_webhook_provider_event')
    )
    op.create_index(op.f('ix_payment_webhook_events_event_id'), 'payment_webhook_events', ['event_id'], unique=False)
    op.create_index(op.f('ix_payment_webhook_events_payment_attempt_id'), 'payment_webhook_events', ['payment_attempt_id'], unique=False)
    op.create_index(op.f('ix_payment_webhook_events_provider'), 'payment_webhook_events', ['provider'], unique=False)
    op.create_index(op.f('ix_payment_webhook_events_status'), 'payment_webhook_events', ['status'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_payment_webhook_events_status'), table_name='payment_webhook_events')
    op.drop_index(op.f('ix_payment_webhook_events_provider'), table_name='payment_webhook_events')
    op.drop_index(op.f('ix_payment_webhook_events_payment_attempt_id'), table_name='payment_webhook_events')
    op.drop_index(op.f('ix_payment_webhook_events_event_id'), table_name='payment_webhook_events')
    op.drop_table('payment_webhook_events')
    op.drop_index('ix_payment_attempt_provider_order', table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_status'), table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_provider_payment_id'), table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_provider_order_id'), table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_provider'), table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_booking_id'), table_name='payment_attempts')
    op.drop_table('payment_attempts')
