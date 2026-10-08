import warnings
from decimal import Decimal
from sqlalchemy import create_engine, Column, Numeric, Integer, MetaData, Table, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass
class T(Base):
    __tablename__='t'
    id: Mapped[int] = mapped_column(primary_key=True)
    price: Mapped[float] = mapped_column(Numeric(10,2))

e = create_engine('sqlite:///:memory:')
Base.metadata.create_all(e)
from sqlalchemy.orm import Session
with Session(e) as s:
    s.add(T(price=Decimal('450.00')))
    s.commit()
    v = s.scalar(select(T.price))
    print('bind Decimal OK; read type:', type(v), repr(v))
    s.add(T(price=800.0))
    s.commit()
    v2 = s.scalar(select(T.price).order_by(T.id.desc()))
    print('bind float OK; read:', type(s.execute(select(T.price)).scalars().all()[-1]))
