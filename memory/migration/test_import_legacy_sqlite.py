from migration.import_legacy_sqlite import prepare_content


def test_prepare_content_omits_file_snapshot_body():
    content = "[AGENTS.md の写し・2026-09-16。正本はファイル]\n\nsecret body"

    result = prepare_content(content, ["AGENTS.md"])

    assert "secret body" not in result
    assert "AGENTS.md" in result
    assert "refs" in result


def test_prepare_content_redacts_assigned_secret():
    result = prepare_content("token=SOM2で読取。X-DA-Token header。", [])

    assert "SOM2" not in result
    assert "token=[REDACTED]" in result
    assert "X-DA-Token" in result
