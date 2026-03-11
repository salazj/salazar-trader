#!/usr/bin/env python3
"""
Bootstrap the project: create directories, copy .env.example, initialize DB.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    print("Bootstrapping $alazar-Trader project...")

    # Create directories
    for d in ["model_artifacts", "reports", "data", "logs"]:
        (ROOT / d).mkdir(exist_ok=True)
        print(f"  Created {d}/")

    # Copy .env.example to .env if not exists
    env_file = ROOT / ".env"
    env_example = ROOT / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        print("  Copied .env.example -> .env")
    else:
        print("  .env already exists, skipping")

    # Initialize database
    import asyncio
    from app.storage.repository import Repository

    async def init_db() -> None:
        repo = Repository(str(ROOT / "salazar-trader.db"))
        await repo.initialize()
        await repo.close()
        print("  Database initialized: salazar-trader.db")

    asyncio.run(init_db())

    print("\nDone! Edit .env with your configuration, then run:")
    print("  python scripts/run_dry_bot.py")


if __name__ == "__main__":
    main()
