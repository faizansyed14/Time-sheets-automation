
def test_parse_upload_text_detects_maternity_leave():
  from app.services.extraction.mock_engine import _parse_upload_text

  text = """
ATTENDANCE SHEET
EMP NO: E2406757
NAME: Anfal Taj
Month: June 2026
1-Jun-2026 Maternity Leave
8-Jun-2026 Sick Leave
22-Jun-2026 9:00 AM 5:30 PM 8
"""
  parsed = _parse_upload_text(text)
  assert parsed is not None
  assert "2026-06-01" in parsed["maternity"]
  assert "2026-06-08" in parsed["sick"]
  assert "2026-06-01" not in parsed["sick"]
