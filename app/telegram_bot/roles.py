from sqlalchemy.orm import Session

from ..models import TelegramUser


def get_user(db: Session, telegram_id: int):
    return db.query(TelegramUser).filter(
        TelegramUser.telegram_id == telegram_id
    ).first()


def create_user(
    db: Session,
    telegram_id: int,
    full_name: str,
    role: str = "employee"
):
    user = TelegramUser(
        telegram_id=telegram_id,
        full_name=full_name,
        role=role
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def get_or_create_user(
    db: Session,
    telegram_id: int,
    full_name: str
):
    user = get_user(db, telegram_id)

    if user:
        return user

    return create_user(
        db,
        telegram_id,
        full_name
    )