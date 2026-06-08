import asyncio
from app.core.database import SessionLocal
from app.models.employee import Employee
from sqlalchemy import select

async def check():
    async with SessionLocal() as db:
        res = await db.execute(select(Employee))
        rows = res.scalars().all()
        print(f"IMPORT_VERIFICATION: {len(rows)} employees in database.")
        
        # Breakdown by location
        dxb = [r for r in rows if r.location == "DXB"]
        auh = [r for r in rows if r.location == "AUH"]
        print(f"DXB: {len(dxb)}, AUH: {len(auh)}")

if __name__ == "__main__":
    asyncio.run(check())
