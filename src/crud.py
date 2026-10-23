from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from . import models, security
from .schemas import UserCreate, UserUpdate


# --- Чтение ---

def get_user_by_username(db: Session, username: str) -> models.User | None:
    return (
        db.query(models.User)
        .options(joinedload(models.User.role))
        .filter(models.User.username == username)
        .first()
    )


def get_user_by_id(db: Session, user_id: int) -> models.User | None:
    return (
        db.query(models.User)
        .options(joinedload(models.User.role))
        .filter(models.User.id == user_id)
        .first()
    )


# --- Создание ---

def create_user(db: Session, user: UserCreate) -> models.User:
    """
    Создаёт пользователя. Ожидается, что user.role_id уже валиден
    (проверка существования роли — на уровне вызывающего кода / эндпоинта).
    """
    hashed_password = security.get_password_hash(user.password)
    db_user = models.User(
        username=user.username,
        password_hash=hashed_password,
        role_id=user.role_id,          # ← было role=user.role
        is_active=user.is_active if user.is_active is not None else True,
    )
    db.add(db_user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Пробрасываем наверх: эндпоинт вернёт 409 Conflict
        raise ValueError("Пользователь с таким логином уже существует") from exc
    db.refresh(db_user)
    return db_user


# --- Обновление ---

# Белый список полей, которые разрешено менять через API
UPDATABLE_FIELDS = {"username", "is_active", "role_id"}


def update_user(db: Session, user_id: int, user_update: UserUpdate) -> models.User | None:
    db_user = get_user_by_id(db, user_id)
    if not db_user:
        return None

    update_data = user_update.model_dump(exclude_unset=True)

    # Пароль меняем отдельно — через хеширование
    new_password = update_data.pop("password", None)
    if new_password:
        db_user.password_hash = security.get_password_hash(new_password)

    # Обновляем только разрешённые поля
    for key, value in update_data.items():
        if key in UPDATABLE_FIELDS:
            setattr(db_user, key, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Логин уже занят другим пользователем") from exc
    db.refresh(db_user)
    return db_user


# --- Удаление (мягкое) ---

def delete_user(db: Session, user_id: int) -> models.User | None:
    db_user = get_user_by_id(db, user_id)
    if not db_user:
        return None

    # Запрет на деактивацию последнего активного админа —
    # защита от потери доступа к системе
    if db_user.role and db_user.role.name == "admin":
        admins_count = (
            db.query(models.User)
            .join(models.Role)
            .filter(
                models.Role.name == "admin",
                models.User.is_active.is_(True),
                models.User.id != user_id,
            )
            .count()
        )
        if admins_count == 0:
            raise ValueError("Нельзя деактивировать последнего администратора")

    db_user.is_active = False
    db.commit()
    return db_user