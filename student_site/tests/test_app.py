"""Integration tests. Database is always copied to tmp_path; original ак.db is untouched."""

import re
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as server
import backup_db

HASH = server.PASSWORDS.hash("CorrectPassword123!")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    copied_db = tmp_path / "ак.db"
    shutil.copy2(server.BASE_DIR / "ак.db", copied_db)
    monkeypatch.setattr(server, "DB_PATH", copied_db)
    server.migrate_database(copied_db)
    with sqlite3.connect(copied_db) as conn:
        conn.execute("UPDATE users SET password_hash = ?", (HASH,))
        conn.execute("DELETE FROM auth_attempts")
    yield copied_db


def csrf_value(response):
    match = re.search(r'name="csrf_token" value="([^\"]+)"', response.text)
    assert match, "CSRF token not found in form"
    return match.group(1)


def login(username):
    client = TestClient(server.app)
    response = client.get("/login")
    token = csrf_value(response)
    response = client.post(
        "/login",
        data={"username": username, "password": "CorrectPassword123!", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    return client


def csrf_for(client, path):
    return csrf_value(client.get(path))


def query(path, sql, args=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, args).fetchall()


def test_healthcheck_reports_ready_database(site):
    response = TestClient(server.app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_migrations_initialize_empty_db_and_are_idempotent(tmp_path):
    empty_db = tmp_path / "new" / "studentflow.db"
    first_run = server.migrate_database(empty_db)
    second_run = server.migrate_database(empty_db)
    assert first_run == ["0001_core", "0002_indexes", "0003_auth_attempts"]
    assert second_run == []
    assert query(empty_db, "PRAGMA integrity_check") == [("ok",)]
    assert query(empty_db, "SELECT COUNT(*) FROM schema_migrations") == [(3,)]
    assert query(empty_db, "SELECT name FROM sqlite_master WHERE type='table' AND name='users'") == [("users",)]


def test_migrations_are_safe_when_two_workers_start_together(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    shared_db = tmp_path / "parallel.db"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(server.migrate_database, [shared_db, shared_db]))
    assert sum(map(len, results)) == 3
    assert query(shared_db, "SELECT COUNT(*) FROM schema_migrations") == [(3,)]


def test_original_data_is_present(site):
    assert len(query(site, "SELECT id FROM users")) == 3
    assert query(site, "SELECT score FROM grades") == [(92,)]
    assert query(site, "PRAGMA integrity_check") == [("ok",)]
    assert query(site, "PRAGMA foreign_key_check") == []


def test_login_and_home_redirect(site):
    client = TestClient(server.app)
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/login"
    page = client.get("/login")
    assert "Қош келдіңіз" in page.text
    bad = client.post("/login", data={"username": "student1", "password": "wrong",
                                        "csrf_token": csrf_value(page)}, follow_redirects=True)
    assert "Логин немесе құпиясөз дұрыс емес" in bad.text
    injection = client.post("/login", data={"username": "' OR 1=1 --", "password": "anything",
        "csrf_token": csrf_value(client.get("/login"))}, follow_redirects=True)
    assert "Логин немесе құпиясөз дұрыс емес" in injection.text
    student = login("student1")
    result = student.get("/dashboard", follow_redirects=True)
    assert result.status_code == 200
    assert "Менің бағаларым" in result.text
    assert "92" in result.text
    token = csrf_value(result)
    logout = student.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert logout.headers["location"] == "/login"
    assert student.get("/student", follow_redirects=False).status_code == 303


def test_role_boundaries(site):
    teacher = login("teacher1")
    student = login("student1")
    admin = login("admin")
    for client, forbidden in [(student, "/teacher"), (admin, "/teacher"), (admin, "/student")]:
        response = client.get(forbidden)
        assert response.status_code == 403, (forbidden, response.text[:300])
    for client in (teacher, student):
        denied_admin = client.get("/admin", follow_redirects=False)
        assert denied_admin.status_code == 303
        assert denied_admin.headers["location"] == "/dashboard"
    assert admin.get("/admin").status_code == 200
    assert teacher.get("/teacher").status_code == 200
    assert student.get("/student").status_code == 200
    no_csrf = teacher.post("/teacher/grades", data={"student_id": 1, "subject_id": 1, "score": 99})
    assert no_csrf.status_code == 403
    assert query(site, "SELECT score FROM grades") == [(92,)]


def test_admin_crud_users_and_subjects(site):
    admin = login("admin")
    csrf = csrf_for(admin, "/admin")
    add = admin.post("/admin/users", data={"username": "newstudent", "full_name": "Жаңа Студент",
        "role": "student", "password": "NewPassword123!", "group_name": "ИС-202", "csrf_token": csrf},
        follow_redirects=True)
    assert add.status_code == 200
    assert "newstudent" in add.text
    user_id = query(site, "SELECT id FROM users WHERE username = 'newstudent'")[0][0]
    assert query(site, "SELECT group_name FROM students WHERE user_id = ?", (user_id,)) == [("ИС-202",)]

    edit_url = f"/admin/users/{user_id}/edit"
    edit_token = csrf_for(admin, edit_url)
    edit = admin.post(edit_url, data={"username": "newstudent", "full_name": "Жаңа Адам",
        "role": "student", "password": "", "group_name": "ИС-203", "csrf_token": edit_token},
        follow_redirects=True)
    assert "Жаңа Адам" in edit.text
    assert query(site, "SELECT group_name FROM students WHERE user_id = ?", (user_id,)) == [("ИС-203",)]

    create_sub = admin.post("/admin/subjects", data={"name": "Алгоритмдер", "teacher_id": 2,
        "csrf_token": csrf_for(admin, "/admin")}, follow_redirects=True)
    assert "Алгоритмдер" in create_sub.text
    sub_id = query(site, "SELECT id FROM subjects WHERE name = 'Алгоритмдер'")[0][0]
    update_sub = admin.post(f"/admin/subjects/{sub_id}/edit", data={"name": "Алгоритмдер 2",
        "teacher_id": 2, "csrf_token": csrf_for(admin, f"/admin/subjects/{sub_id}/edit")},
        follow_redirects=True)
    assert "Алгоритмдер 2" in update_sub.text
    admin.post(f"/admin/subjects/{sub_id}/delete", data={"csrf_token": csrf_for(admin, "/admin")})
    assert not query(site, "SELECT 1 FROM subjects WHERE id=?", (sub_id,))
    admin.post(f"/admin/users/{user_id}/delete", data={"csrf_token": csrf_for(admin, "/admin")})
    assert not query(site, "SELECT 1 FROM users WHERE id=?", (user_id,))
    assert query(site, "PRAGMA foreign_key_check") == []


def test_grade_operations_and_ownership(site):
    teacher = login("teacher1")
    token = csrf_for(teacher, "/teacher")
    added = teacher.post("/teacher/grades", data={"student_id": 1, "subject_id": 1,
        "score": 98, "csrf_token": token}, follow_redirects=True)
    assert added.status_code == 200
    assert "98" in added.text
    grade_id = query(site, "SELECT MAX(id) FROM grades")[0][0]
    grade_url = f"/teacher/grades/{grade_id}/edit"
    updated = teacher.post(grade_url, data={"score": 89,
        "csrf_token": csrf_for(teacher, grade_url)}, follow_redirects=True)
    assert updated.status_code == 200
    assert query(site, "SELECT score FROM grades WHERE id=?", (grade_id,)) == [(89,)]

    with sqlite3.connect(site) as conn:
        conn.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES('teacher2',?,'Other Teacher','teacher')", (HASH,))
        teacher2_id = conn.execute("SELECT id FROM users WHERE username='teacher2'").fetchone()[0]
        conn.execute("INSERT INTO subjects(name,teacher_id) VALUES('Other Subject',?)", (teacher2_id,))
        foreign_subject_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO grades(student_id,subject_id,score) VALUES(1,?,77)", (foreign_subject_id,))
        foreign_grade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    teacher2 = login("teacher2")
    assert teacher.get(f"/teacher/grades/{foreign_grade_id}/edit").status_code == 404
    assert teacher.post(f"/teacher/grades/{foreign_grade_id}/delete",
                        data={"csrf_token": csrf_for(teacher, "/teacher")}).status_code == 404
    blocked = teacher.post("/teacher/grades", data={"student_id":1, "subject_id": foreign_subject_id,
        "score":90, "csrf_token": csrf_for(teacher, "/teacher")}, follow_redirects=True)
    assert "дұрыс таңдалмаған" in blocked.text
    assert teacher2.get(f"/teacher/grades/{foreign_grade_id}/edit").status_code == 200

    deleted = teacher.post(f"/teacher/grades/{grade_id}/delete",
        data={"csrf_token": csrf_for(teacher, "/teacher")}, follow_redirects=True)
    assert deleted.status_code == 200
    assert not query(site, "SELECT 1 FROM grades WHERE id=?", (grade_id,))
    assert query(site, "PRAGMA foreign_key_check") == []


def test_student_cannot_see_other_students_results(site):
    with sqlite3.connect(site) as conn:
        conn.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES('student2',?,'Жасырын Студент','student')", (HASH,))
        id2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO students(user_id,group_name) VALUES(?, 'Жасырын-999')", (id2,))
        student2_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO grades(student_id,subject_id,score) VALUES(?,1,17)", (student2_id,))
    client = login("student1")
    response = client.get("/student")
    assert response.status_code == 200
    assert "92" in response.text
    assert "Жасырын Студент" not in response.text
    assert "Жасырын-999" not in response.text
    assert ">17<" not in response.text


def test_reject_invalid_score_and_guard_history(site):
    teacher = login("teacher1")
    for score in [101, -5]:
        response = teacher.post("/teacher/grades", data={"student_id": 1, "subject_id": 1,
            "score": score, "csrf_token": csrf_for(teacher, "/teacher")}, follow_redirects=True)
        assert "0 мен 100" in response.text
    assert query(site, "SELECT score FROM grades") == [(92,)]
    admin = login("admin")
    blocked_user = admin.post("/admin/users/3/delete", data={"csrf_token": csrf_for(admin, "/admin")},
        follow_redirects=True)
    assert "Студенттің бағалары бар" in blocked_user.text
    blocked_subject = admin.post("/admin/subjects/1/delete", data={"csrf_token": csrf_for(admin, "/admin")},
        follow_redirects=True)
    assert "Бағалары бар пәнді" in blocked_subject.text
    assert query(site, "SELECT count(*) FROM grades")[0][0] == 1


def test_password_hash_not_plaintext(site):
    admin = login("admin")
    token = csrf_for(admin, "/admin")
    admin.post("/admin/users", data={"username": "tempteacher", "full_name": "New Teacher",
        "role": "teacher", "password": "SecurePassword!", "csrf_token": token})
    secret_hash = query(site, "SELECT password_hash FROM users WHERE username='tempteacher'")[0][0]
    assert secret_hash.startswith("$argon2id$")
    assert "SecurePassword!" not in secret_hash
    assert server.PASSWORDS.verify(secret_hash, "SecurePassword!")


@pytest.mark.parametrize("path", ["/lab10", "/lab10/plan", "/lab10/map"])
def test_lab10_requires_login(site, path):
    client = TestClient(server.app)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("username", ["admin", "teacher1", "student1"])
def test_lab10_all_roles_and_database_unchanged(site, username):
    before = site.read_bytes()
    client = login(username)
    for path, expected in [("/lab10", 'id="plan-form"'),
                           ("/lab10/plan", 'id="plan-rows"'),
                           ("/lab10/map", "Architecture Map")]:
        response = client.get(path)
        assert response.status_code == 200
        assert expected.lower() in response.text.lower()
        assert 'href="/lab10"' in response.text
        assert 'href="/lab10/plan"' in response.text
        assert 'href="/lab10/map"' in response.text
    assert site.read_bytes() == before


def test_lab10_does_not_add_server_writes(site):
    client = login("student1")
    before = site.read_bytes()
    for path in ["/lab10", "/lab10/plan", "/lab10/map"]:
        assert client.post(path, data={"hours": 3}).status_code == 405
    assert site.read_bytes() == before


def test_lab10_script_is_separate_and_no_hardcoded_cards(site):
    client = login("student1")
    editor = client.get("/lab10").text
    assert 'id="course-catalog" class="lab-catalog"></div>' in editor
    assert 'src="/static/lab10.js"' in editor
    script = client.get("/static/lab10.js")
    assert script.status_code == 200
    assert "localStorage.setItem(STORAGE_KEY" in script.text
    assert "localStorage.getItem(STORAGE_KEY" in script.text
    assert "new FormData(form)" in script.text


def test_registration_assigns_student_role_and_hashes_password(site):
    client = TestClient(server.app)
    page = client.get("/register")
    token = csrf_value(page)
    result = client.post("/register", data={"username": "newperson", "full_name": "Жаңа Студент",
        "password": "SafePassword123!", "password_confirm": "SafePassword123!",
        "role": "admin", "group_name": "ИС-221", "csrf_token": token}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/dashboard"
    welcome = client.get("/dashboard", follow_redirects=True)
    assert "Жаңа Студент" in welcome.text
    row = query(site, "SELECT role, password_hash FROM users WHERE username = ?", ("newperson",))[0]
    assert row[0] == "student"
    assert row[1] != "SafePassword123!"
    assert server.PASSWORDS.verify(row[1], "SafePassword123!")
    assert query(site, "SELECT group_name FROM students s JOIN users u ON u.id=s.user_id WHERE u.username=?",
                 ("newperson",)) == [("ИС-221",)]


def test_sql_injection_input_is_bound_as_plain_search_text(site):
    client = login("student1")
    response = client.post("/lab12/security-test", data={"search": "' OR 1=1 --",
        "csrf_token": csrf_for(client, "/lab12")})
    assert response.status_code == 200
    assert "Сәйкес жазба саны: 0" in response.text
    assert "@admin" not in response.text
    assert query(site, "SELECT COUNT(*) FROM users")[0][0] == 3


def test_password_change_requires_old_password_and_rehashes(site):
    client = login("student1")
    second_session = login("student1")
    page = client.get("/account")
    csrf = csrf_value(page)
    denied = client.post("/account/password", data={"current_password": "wrong", "new_password": "DifferentPass123!",
        "password_confirm": "DifferentPass123!", "csrf_token": csrf}, follow_redirects=True)
    assert "Қазіргі құпиясөз дұрыс емес" in denied.text
    assert query(site, "SELECT password_hash FROM users WHERE username='student1'")[0][0] != "DifferentPass123!"

    updated = client.post("/account/password", data={"current_password": "CorrectPassword123!",
        "new_password": "DifferentPass123!", "password_confirm": "DifferentPass123!",
        "csrf_token": csrf}, follow_redirects=False)
    assert updated.status_code == 303
    assert updated.headers["location"] == "/login"
    stored = query(site, "SELECT password_hash FROM users WHERE username='student1'")[0][0]
    assert server.PASSWORDS.verify(stored, "DifferentPass123!")
    assert client.get("/student", follow_redirects=False).headers["location"] == "/login"
    assert second_session.get("/student", follow_redirects=False).headers["location"] == "/login"


def test_login_rate_limit_and_security_headers(site):
    client = TestClient(server.app)
    page = client.get("/login")
    token = csrf_value(page)
    for _ in range(server.LOGIN_MAX_ATTEMPTS):
        result = client.post("/login", data={"username": "locked-user", "password": "wrong",
            "csrf_token": token}, follow_redirects=False)
        assert result.status_code == 303
    limited = client.post("/login", data={"username": "locked-user", "password": "wrong",
        "csrf_token": token}, follow_redirects=True)
    assert "уақытша шектелді" in limited.text
    user_key = server.login_rate_key("user", "locked-user")
    assert query(site, "SELECT failures FROM auth_attempts WHERE rate_key = ?", (user_key,)) == [(server.LOGIN_MAX_ATTEMPTS,)]
    assert query(site, "SELECT 1 FROM auth_attempts WHERE rate_key = 'locked-user'") == []
    assert limited.headers["x-content-type-options"] == "nosniff"
    assert limited.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "object-src 'none'" in limited.headers["content-security-policy"]


def test_database_backup_is_consistent_and_does_not_modify_source(site, tmp_path):
    destination = tmp_path / "safe" / "studentflow-backup.db"
    result = backup_db.backup_database(site, destination)
    assert result == destination
    assert query(result, "PRAGMA integrity_check") == [("ok",)]
    assert len(query(result, "SELECT id FROM users")) == 3
    assert query(site, "SELECT score FROM grades") == [(92,)]
    with pytest.raises(ValueError):
        backup_db.backup_database(site, site)


def test_backup_retention_only_prunes_old_studentflow_backups(tmp_path):
    import os
    import time

    old_copy = tmp_path / "studentflow-old.db"
    recent_copy = tmp_path / "studentflow-new.db"
    unrelated = tmp_path / "important.db"
    for path in (old_copy, recent_copy, unrelated):
        path.write_bytes(b"test")
    old_time = time.time() - 40 * 24 * 60 * 60
    os.utime(old_copy, (old_time, old_time))
    assert backup_db.prune_old_backups(tmp_path, keep_days=30) == 1
    assert not old_copy.exists()
    assert recent_copy.exists() and unrelated.exists()


def test_not_found_route_renders_recovery_page(site):
    client = TestClient(server.app)
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert "Бет табылмады" in response.text
    assert "href=\"/register\"" in response.text
    assert response.headers["content-security-policy"]


def test_admin_and_teacher_search_filters(site):
    admin = login("admin")
    filtered_admin = admin.get("/admin?q=student1")
    assert filtered_admin.status_code == 200
    assert "@student1" in filtered_admin.text
    assert "@teacher1" not in filtered_admin.text

    teacher = login("teacher1")
    filtered_teacher = teacher.get("/teacher", params={"q": "Ермеков", "subject_id": 1})
    assert filtered_teacher.status_code == 200
    assert "Ермеков Али" in filtered_teacher.text
    assert '<span class="score-badge">92</span>' in filtered_teacher.text
    empty = teacher.get("/teacher?q=no-such-person")
    assert "Сүзгіге сәйкес баға табылмады" in empty.text


@pytest.mark.parametrize("ancestors", ["'self'", "'none'", "'self' https://preview.example"])
def test_frame_ancestors_header(site, monkeypatch, ancestors):
    monkeypatch.setattr(server, "CSP_FRAME_ANCESTORS", ancestors)
    response = TestClient(server.app).get("/login")
    assert f"frame-ancestors {ancestors}" in response.headers["content-security-policy"]


@pytest.mark.parametrize("ancestors", [None, "", "https://preview.example", "bad;default-src *", "bad\r\nx-test: yes"])
def test_production_frame_configuration(ancestors):
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env.update(APP_ENV="production", COOKIE_SECURE="1", COOKIE_SAMESITE="lax",
               SESSION_SECRET="test-production-secret-not-for-deployment-1234")
    env.pop("CSP_FRAME_ANCESTORS", None)
    if ancestors is not None:
        env["CSP_FRAME_ANCESTORS"] = ancestors
    result = subprocess.run(
        [sys.executable, "-c", "import app; print(app.CSP_FRAME_ANCESTORS)"],
        cwd=server.BASE_DIR, env=env, capture_output=True, text=True, timeout=15, check=False,
    )
    if ancestors is None or ancestors == "https://preview.example":
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (ancestors or "'self'")
    else:
        assert result.returncode != 0
        assert "CSP_FRAME_ANCESTORS" in result.stderr


def test_backup_cli_rejects_invalid_retention_before_writing(tmp_path):
    import subprocess
    import sys

    destination = tmp_path / "must-not-exist.db"
    result = subprocess.run(
        [sys.executable, str(server.BASE_DIR / "backup_db.py"), str(destination), "--keep-days", "0"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert "--keep-days must be at least 1" in result.stderr
    assert not destination.exists()
