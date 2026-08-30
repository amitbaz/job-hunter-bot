from job_hunter.pdf import render_cover_letter_pdf


def test_render_pdf_has_pdf_signature(tmp_path):
    path = render_cover_letter_pdf("Amit Baz\n\nDear Hiring Team,\nHello.", "Acme", "Senior Product Engineer", tmp_path)
    assert path.name == "Acme_Senior_Product_Engineer_Cover_Letter.pdf"
    assert path.read_bytes().startswith(b"%PDF")


def test_render_pdf_sanitizes_filename(tmp_path):
    path = render_cover_letter_pdf("Hello.", "Acme & Co.! ", "Staff / Engineer", tmp_path)
    assert path.name == "Acme_Co._Staff_Engineer_Cover_Letter.pdf"
    assert path.exists()


def test_render_pdf_escapes_special_characters(tmp_path):
    path = render_cover_letter_pdf("Dear <Team> & 'friends'", "Acme", "Engineer", tmp_path)
    assert path.read_bytes().startswith(b"%PDF")
