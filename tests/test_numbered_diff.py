"""number_diff: the diff as the model sees it, with old and new line numbers on every line.

Models are bad at counting lines, so the prompt shows the numbers and the model copies them.
"""

from reviewer.diff import number_diff

MODIFIED = """\
diff --git a/app/views.py b/app/views.py
index 1111111..2222222 100644
--- a/app/views.py
+++ b/app/views.py
@@ -10,3 +10,4 @@ def signup(request):
     user = create_user(form)
-    user.save()
+    user.save(update_fields=["name"])
+    logger.info("signup %s", user.email)
     return redirect("home")
"""

DELETED = """\
diff --git a/app/jobs.py b/app/jobs.py
deleted file mode 100644
--- a/app/jobs.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def purge():
-    User.objects.filter(stale=True).delete()
"""

ADDED = """\
diff --git a/app/new.py b/app/new.py
new file mode 100644
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,1 @@
+X = 1
"""

RENAMED = """\
diff --git a/app/old_name.py b/app/new_name.py
similarity index 90%
rename from app/old_name.py
rename to app/new_name.py
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -1,2 +1,2 @@
 A = 1
-B = 2
+B = 3
"""


def test_every_line_carries_its_old_and_new_number() -> None:
    lines = number_diff(MODIFIED).splitlines()

    assert lines == [
        "=== app/views.py (modified) ===",
        "@@ -10,3 +10,4 @@ def signup(request):",
        "     10    10 |     user = create_user(form)",
        "-    11       |     user.save()",
        '+          11 |     user.save(update_fields=["name"])',
        '+          12 |     logger.info("signup %s", user.email)',
        '     12    13 |     return redirect("home")',
    ]


def test_deleted_file_is_keyed_by_old_path_and_shows_old_numbers() -> None:
    text = number_diff(DELETED)

    assert "=== app/jobs.py (deleted) ===" in text
    assert "-     1       | def purge():" in text
    assert "-     2       |     User.objects.filter(stale=True).delete()" in text


def test_new_file() -> None:
    text = number_diff(ADDED)

    assert "=== app/new.py (new file) ===" in text
    assert "+           1 | X = 1" in text


def test_renamed_file_shows_both_paths_and_is_keyed_by_new_path() -> None:
    assert "=== app/new_name.py (renamed from app/old_name.py) ===" in number_diff(RENAMED)


def test_files_are_separated_by_a_blank_line() -> None:
    text = number_diff(MODIFIED + ADDED)

    assert "\n\n=== app/new.py (new file) ===" in text
