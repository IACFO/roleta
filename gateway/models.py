from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, JSON, DateTime, text, ForeignKey

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    okta_user_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[str] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    access_expires_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)  # NOVO

    store = relationship("Store", back_populates="user", uselist=False)

class Store(Base):
    __tablename__ = "store"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    user = relationship("User", back_populates="store")
