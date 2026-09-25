"""Explicitly create missing application tables for a new database."""

from app.db import engine
from app.models import SQLModel


def main() -> None:
    SQLModel.metadata.create_all(engine)
    print("Database tables are ready. No existing tables were altered.")


if __name__ == "__main__":
    main()
