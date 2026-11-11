from mcp_utils.timezones import convert_time


def test_karachi_to_london() -> None:
    out = convert_time("2026-11-11T15:00", "Asia/Karachi", "Europe/London")
    assert "2026-11-11T10:00" in out
    assert "Wednesday" in out


def test_time_only_means_today() -> None:
    out = convert_time("15:00", "Asia/Karachi", "Europe/London")
    assert "T15:00" in out


def test_unknown_zone() -> None:
    out = convert_time("15:00", "Mars/Base", "Europe/London")
    assert out == "Error: unknown timezone 'Mars/Base'. Use an IANA name like 'Asia/Karachi'."


def test_bad_time() -> None:
    out = convert_time("teatime", "Asia/Karachi", "Europe/London")
    assert out == ("Error: couldn't read the time 'teatime'. Use ISO format like 2026-11-11T15:00.")
