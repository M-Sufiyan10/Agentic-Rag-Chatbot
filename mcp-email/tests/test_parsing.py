from mcp_email.mailbox import parse_message


def raw(headers: str, body: str) -> bytes:
    return (headers + "\r\n\r\n" + body).encode()


H = "From: a@example.com\r\nTo: b@example.com\r\nSubject: S\r\nDate: Mon, 1 Jan 2024 10:00:00 +0000"


def test_encoded_subject_and_sender_name_are_decoded() -> None:
    h = (
        "From: =?UTF-8?Q?Sara_Kh=C3=A1n?= <SARA@Example.com>\r\nTo: b@example.com\r\n"
        "Subject: =?UTF-8?B?w5xiZXI=?=\r\nContent-Type: text/plain; charset=utf-8"
    )
    m = parse_message("1", raw(h, "hi"))
    assert m.subject == "Über"
    assert m.from_name == "Sara Khán"
    assert m.from_address == "sara@example.com"


def test_multipart_alternative_prefers_plain_text() -> None:
    h = H + '\r\nContent-Type: multipart/alternative; boundary="B"'
    body = (
        "--B\r\nContent-Type: text/html\r\n\r\n<p>html version</p>\r\n"
        "--B\r\nContent-Type: text/plain\r\n\r\nplain version\r\n--B--\r\n"
    )
    assert parse_message("1", raw(h, body)).body.strip() == "plain version"


def test_html_only_body_becomes_text() -> None:
    h = H + "\r\nContent-Type: text/html"
    m = parse_message("1", raw(h, "<p>Hello <b>Bob</b></p><script>x()</script>"))
    assert "Hello Bob" in m.body
    assert "<" not in m.body
    assert "x()" not in m.body


def test_long_body_truncated_to_8000() -> None:
    h = H + "\r\nContent-Type: text/plain"
    m = parse_message("1", raw(h, "a" * 9000))
    assert m.truncated is True
    assert len(m.body) <= 8000 + 200
    assert "truncated" in m.body.splitlines()[-1].lower()


def test_attachments_listed_by_name() -> None:
    h = H + '\r\nContent-Type: multipart/mixed; boundary="B"'
    body = (
        "--B\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
        "--B\r\nContent-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="invoice.pdf"\r\n'
        "Content-Transfer-Encoding: base64\r\n\r\nSEVMTE9fQVRUQUNI\r\n--B--\r\n"
    )
    m = parse_message("1", raw(h, body))
    assert m.attachments == ["invoice.pdf"]
    assert "SEVMTE9" not in m.body
    assert "see attached" in m.body
