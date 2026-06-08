import asyncio
import os
from app.core.database import SessionLocal, engine
from app.services.employee_import import import_employees_from_bytes

async def run_seed():
    filepath = r"c:\Users\FAIZAN\Downloads\timesheet-portal\backend\app\seed\data\Employee_details.xlsx.xlsx"
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return

    print(f"Reading {filepath}...")
    with open(filepath, "rb") as f:
        data = f.read()

    async with SessionLocal() as db:
        print("Importing employees...")
        summary = await import_employees_from_bytes(db, data)
        print(f"Import complete: {summary}")

if __name__ == "__main__":
    asyncio.run(run_seed())
