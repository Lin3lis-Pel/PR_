"""parse_diff: which lines a diff changes and shows, per file and side.

Files are keyed the way GitHub review comments key them: by the new path, or by the old
path when the file is deleted.
"""

import pytest

from reviewer.diff import DiffError, parse_diff
from reviewer.schemas import Side

ADDED_LINES = """\
diff --git a/app/views.py b/app/views.py
index 1111111..2222222 100644
--- a/app/views.py
+++ b/app/views.py
@@ -10,4 +10,6 @@ def signup(request):
     user = create_user(form)
     user.save()
+    logger.info("signup %s", user.email)
+    analytics.track("signup", {"email": user.email})
     return redirect("home")
 # end
"""

REMOVED_LINES = """\
diff --git a/app/users.py b/app/users.py
index 1111111..2222222 100644
--- a/app/users.py
+++ b/app/users.py
@@ -20,5 +20,3 @@ class UserService:
     def delete(self, user):
-        anonymise(user)
-        purge_backups(user)
         user.delete()
     # end
"""

NEW_FILE = """\
diff --git a/app/models.py b/app/models.py
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/app/models.py
@@ -0,0 +1,3 @@
+class Customer(models.Model):
+    email = models.EmailField()
+    iban = models.CharField(max_length=34)
"""

DELETED_FILE = """\
diff --git a/app/anonymise.py b/app/anonymise.py
deleted file mode 100644
index 3333333..0000000
--- a/app/anonymise.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def anonymise(user):
-    user.email = None
-    user.save()
"""

RENAMED_FILE = """\
diff --git a/app/old_name.py b/app/new_name.py
similarity index 90%
rename from app/old_name.py
rename to app/new_name.py
index 5555555..6666666 100644
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -1,3 +1,3 @@
 import logging
-log = logging.getLogger("old")
+log = logging.getLogger("new")
 # end
"""

TWO_HUNKS = """\
diff --git a/app/api.py b/app/api.py
index 1111111..2222222 100644
--- a/app/api.py
+++ b/app/api.py
@@ -1,2 +1,3 @@
 import requests
+import analytics
 # first
@@ -50,3 +51,3 @@ def send(user):
     payload = {}
-    payload["id"] = user.id
+    payload["email"] = user.email
     requests.post(URL, json=payload)
"""


def test_added_lines_are_changed_on_the_new_side() -> None:
    diff = parse_diff(ADDED_LINES)

    assert diff.files == frozenset({"app/views.py"})
    assert diff.changed_lines("app/views.py", Side.NEW) == {12, 13}
    assert diff.changed_lines("app/views.py", Side.OLD) == set()


def test_context_lines_are_visible_but_not_changed() -> None:
    diff = parse_diff(ADDED_LINES)

    assert diff.visible_lines("app/views.py", Side.NEW) == {10, 11, 12, 13, 14, 15}
    assert diff.visible_lines("app/views.py", Side.OLD) == {10, 11, 12, 13}


def test_removed_lines_are_changed_on_the_old_side() -> None:
    diff = parse_diff(REMOVED_LINES)

    assert diff.changed_lines("app/users.py", Side.OLD) == {21, 22}
    assert diff.changed_lines("app/users.py", Side.NEW) == set()


def test_new_file_has_only_new_side_lines() -> None:
    diff = parse_diff(NEW_FILE)

    assert diff.changed_lines("app/models.py", Side.NEW) == {1, 2, 3}
    assert diff.visible_lines("app/models.py", Side.OLD) == set()


def test_deleted_file_is_keyed_by_its_old_path() -> None:
    diff = parse_diff(DELETED_FILE)

    assert diff.files == frozenset({"app/anonymise.py"})
    assert diff.changed_lines("app/anonymise.py", Side.OLD) == {1, 2, 3}


def test_renamed_file_is_keyed_by_its_new_path_on_both_sides() -> None:
    diff = parse_diff(RENAMED_FILE)

    assert diff.files == frozenset({"app/new_name.py"})
    assert diff.changed_lines("app/new_name.py", Side.OLD) == {2}
    assert diff.changed_lines("app/new_name.py", Side.NEW) == {2}


def test_lines_from_every_hunk_are_collected() -> None:
    diff = parse_diff(TWO_HUNKS)

    assert diff.changed_lines("app/api.py", Side.NEW) == {2, 52}
    assert diff.changed_lines("app/api.py", Side.OLD) == {51}


def test_several_files_in_one_diff() -> None:
    diff = parse_diff(ADDED_LINES + REMOVED_LINES + NEW_FILE)

    assert diff.files == frozenset({"app/views.py", "app/users.py", "app/models.py"})


def test_unknown_file_has_no_lines() -> None:
    diff = parse_diff(ADDED_LINES)

    assert diff.changed_lines("app/other.py", Side.NEW) == set()
    assert diff.visible_lines("app/other.py", Side.NEW) == set()


def test_empty_diff_has_no_files() -> None:
    assert parse_diff("").files == frozenset()


def test_malformed_hunk_raises_diff_error() -> None:
    broken = ADDED_LINES.replace("@@ -10,4 +10,6 @@", "@@ -10,40 +10,60 @@")

    with pytest.raises(DiffError):
        parse_diff(broken)
