"""Extract Email constants."""
from __future__ import annotations

TAG_PREFIX = "__email_extract__"

BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday", "other")
# Not leave, but tracked the same way so every calendar day can be accounted
# for (see grouping.normalise_sheet's day-by-day accounting).
DAY_FIELDS = ("working_days", "weekend_days")
