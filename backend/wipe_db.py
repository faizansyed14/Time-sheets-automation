import asyncio
from sqlalchemy import text
from app.core.database import SessionLocal, engine

async def clear_database():
    print("Connecting to database to clear tables...")
    async with engine.begin() as conn:
        # Disable foreign key checks for SQLite
        await conn.execute(text("PRAGMA foreign_keys = OFF;"))
        
        # Get list of all tables
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"))
        tables = [row[0] for row in result]
        
        print(f"Found tables: {', '.join(tables)}")
        
        for table in tables:
            print(f"Clearing table: {table}")
            await conn.execute(text(f"DELETE FROM {table};"))
            # Reset sqlite_sequence for that table if it exists
            try:
                await conn.execute(text(f"DELETE FROM sqlite_sequence WHERE name='{table}';"))
            except Exception:
                pass
            
        await conn.execute(text("PRAGMA foreign_keys = ON;"))
        print("Database tables cleared successfully.")

if __name__ == "__main__":
    asyncio.run(clear_database())
