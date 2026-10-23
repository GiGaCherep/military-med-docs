import os
import sys

# --- НАЧАЛО БЛОКА ДЛЯ ПОДКЛЮЧЕНИЯ МОДУЛЕЙ ---
src_path = os.path.dirname(os.path.abspath(__file__))
root_path = os.path.dirname(src_path)

for path in (root_path, src_path):
    if path not in sys.path:
        sys.path.insert(0, path)
# --- КОНЕЦ БЛОКА ---

from sqlalchemy import select
from sqlalchemy.orm import joinedload, Session

from database import engine
from models import User


def main() -> None:
    with Session(engine) as session:
        stmt = (
            select(User)
            .options(joinedload(User.role))
            .where(User.is_active.is_(True))
            .order_by(User.id)
        )
        users = session.execute(stmt).scalars().all()

        print(f"Всего активных пользователей: {len(users)}")
        if not users:
            print("⚠️ Активных пользователей нет. Запустите create_admin.py.")
            return

        for user in users:
            role_name = user.role.name if user.role else "N/A"
            print(
                f"- ID: {user.id}, "
                f"Логин: {user.username}, "
                f"Роль: {role_name}, "
                f"Активен: {user.is_active}"
            )


if __name__ == "__main__":
    main()