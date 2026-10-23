import os
import sys
import re
import getpass
import logging
from pathlib import Path

# --- Блок подключения модулей (работает из любой папки) ---
_file = Path(__file__).resolve()
for p in (_file.parent, _file.parent.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# --- Конец блока ---

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine, Base
from models import User, Role
from security import get_password_hash

logging.basicConfig(
    filename="admin_creation.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

USERNAME_RE = re.compile(r"[a-zA-Z0-9._-]{3,32}")
PWD_CHECKS = [
    (re.compile(r"[A-ZА-Я]"), "заглавная буква"),
    (re.compile(r"[a-zа-я]"), "строчная буква"),
    (re.compile(r"\d"), "цифра"),
    (re.compile(r"[!@#$%^&*()_\-+=\[\]{};:'\",.<>/?\\|`~]"), "спецсимвол"),
]


def validate_password(pwd: str) -> list[str]:
    errors = []
    if len(pwd) < 12:
        errors.append("минимум 12 символов")
    for pattern, name in PWD_CHECKS:
        if not pattern.search(pwd):
            errors.append(name)
    return errors


def get_or_create_admin_role() -> int:
    """Возвращает id роли 'admin', создавая её при необходимости."""
    with Session(engine) as session, session.begin():
        role = session.execute(
            select(Role).where(Role.name == "admin")
        ).scalar_one_or_none()

        if role is None:
            role = Role(name="admin")
            session.add(role)
            session.flush()  # получаем role.id до коммита

        return role.id


def create_admin() -> None:
    # ВАЖНО: БД должна быть инициализирована заранее через init_db.py или миграции.
    # Base.metadata.create_all(bind=engine) здесь не вызываем намеренно.

    role_id = get_or_create_admin_role()

    # Шаг 1: ввод и валидация логина
    username = input("Логин администратора: ").strip()
    if not USERNAME_RE.fullmatch(username):
        print("❌ Логин: 3–32 символа, только латиница/цифры/._-")
        return

    # Шаг 2: проверка уникальности логина (по username, а не по роли!)
    with Session(engine) as session:
        if session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none():
            print("❌ Пользователь с таким логином уже существует.")
            return

    # Шаг 3: ввод и валидация пароля
    password = getpass.getpass("Пароль: ").strip()
    errors = validate_password(password)
    if errors:
        print("❌ Пароль слишком слабый:", ", ".join(errors))
        return

    password_confirm = getpass.getpass("Повторите пароль: ").strip()
    if password != password_confirm:
        print("❌ Пароли не совпадают.")
        return

    password_hash = get_password_hash(password)

    # Шаг 4: создание пользователя
    with Session(engine) as session, session.begin():
        session.add(User(
            username=username,
            password_hash=password_hash,
            role_id=role_id,
            is_active=True,
        ))

    logging.info("Создан администратор: %s", username)
    print("✅ Администратор успешно создан.")


if __name__ == "__main__":
    create_admin()