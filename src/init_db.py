import sys
from pathlib import Path

_file = Path(__file__).resolve()
for p in (str(_file.parent), str(_file.parent / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Base, engine
from models import Role

BASE_ROLES = ["admin", "doctor", "commander"]


def seed_roles() -> None:
    with Session(engine) as session, session.begin():
        existing = {r.name for r in session.execute(select(Role)).scalars()}
        for name in BASE_ROLES:
            if name not in existing:
                session.add(Role(name=name))
                print(f"  + создана роль: {name}")


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("✅ Таблицы созданы (или уже существовали).")
    seed_roles()
    print("✅ Базовые роли готовы.")