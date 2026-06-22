#!/usr/bin/env python3
"""Load demo employees from mock_data into the Employee Matcher table."""
from app.seed.seed_employee_matcher import main
import asyncio

asyncio.run(main())
