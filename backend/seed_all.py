import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.database import SessionLocal, engine
from app.models.email_message import EmailMessage, EmailStatus
from app.seed.mock_data import MESSAGES, EMPLOYEE_MATCHER
from app.seed.seed_employee_matcher import seed_employee_matcher

async def seed_data():
    print("Connecting to database to seed data...")
    async with SessionLocal() as db:
        # 1. Seed Employee Matcher
        print("Seeding employee matcher...")
        await seed_employee_matcher(db)
        
        # 2. Seed Email Inbox
        print("Seeding email inbox...")
        # Check if we already have messages to avoid duplicates
        existing = (await db.execute(select(EmailMessage.provider_message_id))).scalars().all()
        
        count = 0
        for m in MESSAGES:
            mid = m["message_id"]
            if mid in existing:
                continue
                
            msg = EmailMessage(
                provider_message_id=mid,
                sender_name=m["sender_name"],
                sender_email=m["sender_email"],
                subject=m["subject"],
                received_at=m["received_at"],
                body_text=m["body_text"],
                status=EmailStatus.NEW,
                has_approval_screenshot=bool(m.get("approval")),
                attachments=[
                    {"attachment_id": f"{mid}_ts", "filename": f"timesheet.{m['cases'][0]['doc']}", "kind": "timesheet"},
                    {"attachment_id": f"{mid}_ap", "filename": "approval.png", "kind": "approval_screenshot"}
                ]
            )
            db.add(msg)
            count += 1
            
        await db.commit()
        print(f"Seeded {count} email messages.")
        print("Data seeding completed successfully.")

if __name__ == "__main__":
    asyncio.run(seed_data())
