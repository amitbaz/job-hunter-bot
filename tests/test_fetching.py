from job_hunter.fetching import extract_job_from_html


def test_extracts_jobposting_json_ld():
    html = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Senior Product Engineer","description":"<p>React and TypeScript</p>","hiringOrganization":{"name":"Acme"},"jobLocationType":"TELECOMMUTE"}</script>'''
    data = extract_job_from_html(html)
    assert data["title"] == "Senior Product Engineer"
    assert data["company"] == "Acme"
    assert data["remote"] is True
    assert "React and TypeScript" in data["description"]


def test_extracts_json_ld_from_array():
    html = '''<script type="application/ld+json">[{"@type":"WebPage"},{"@type":"JobPosting","title":"Frontend Lead","description":"TypeScript","hiringOrganization":{"name":"Corp"}}]</script>'''
    data = extract_job_from_html(html)
    assert data["title"] == "Frontend Lead"


def test_falls_back_to_page_title():
    html = '''<html><head><title>Senior Engineer at Acme</title></head><body><p>Some job description here with React</p></body></html>'''
    data = extract_job_from_html(html)
    assert data.get("title") or data.get("description")
