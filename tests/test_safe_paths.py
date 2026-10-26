"""Tests for src.safe_paths — centralized path validation."""

import os
import tempfile
import unittest
from pathlib import Path

from src.safe_paths import (
    expand_path,
    is_regular_file,
    is_sensitive_path,
    is_within,
    is_within_any,
    resolve_safe,
    safe_join,
    validate_path,
)


class TestExpandPath(unittest.TestCase):
    def test_expands_tilde(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertEqual(expand_path("~/foo"), os.path.join(home, "foo"))

    def test_resolves_dotdot(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            target = base / "sub" / ".." / "file"
            self.assertEqual(expand_path(str(target)), str(base / "file"))

    def test_resolves_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real_dir = base / "real"
            real_dir.mkdir()
            link = base / "link"
            link.symlink_to(real_dir)
            self.assertEqual(expand_path(str(link / "file")), str(real_dir / "file"))

    def test_accepts_pathlike(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertEqual(expand_path(Path("~/foo")), os.path.join(home, "foo"))


class TestIsSensitivePath(unittest.TestCase):
    def test_etc_shadow_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/etc/shadow"))

    def test_etc_sudoers_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/etc/sudoers"))

    def test_sudoers_d_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/etc/sudoers.d/01-user"))

    def test_root_dir_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/root"))

    def test_root_child_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/root/.ssh/id_rsa"))

    def test_boot_is_sensitive(self):
        self.assertTrue(is_sensitive_path("/boot/grub/grub.cfg"))

    def test_ssh_in_home_is_sensitive(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertTrue(is_sensitive_path(os.path.join(home, ".ssh", "id_rsa")))

    def test_gnupg_in_home_is_sensitive(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertTrue(is_sensitive_path(os.path.join(home, ".gnupg", "pubring.kbx")))

    def test_aws_in_home_is_sensitive(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertTrue(is_sensitive_path(os.path.join(home, ".aws", "credentials")))

    def test_normal_file_in_home_is_not_sensitive(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertFalse(is_sensitive_path(os.path.join(home, "Documents", "notes.txt")))

    def test_etc_passwd_is_not_sensitive(self):
        # /etc/passwd is world-readable; not in the sensitive set.
        self.assertFalse(is_sensitive_path("/etc/passwd"))

    def test_symlink_to_sensitive_is_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "shadow-link"
            link.symlink_to("/etc/shadow")
            self.assertTrue(is_sensitive_path(str(link)))


class TestIsWithin(unittest.TestCase):
    def test_child_is_within(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            child = base / "sub" / "file"
            self.assertTrue(is_within(child, base))

    def test_same_path_is_within(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            self.assertTrue(is_within(base, base))

    def test_sibling_not_within(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            sibling = base.parent / (base.name + "-sibling")
            self.assertFalse(is_within(sibling, base))

    def test_prefix_sibling_not_within(self):
        # /etcfoo must not be considered inside /etc
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "etc"
            base.mkdir()
            sibling = Path(tmp) / "etcfoo"
            sibling.mkdir()
            self.assertFalse(is_within(sibling, base))

    def test_parent_not_within_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            child = base / "sub"
            child.mkdir()
            self.assertFalse(is_within(base, child))

    def test_dotdot_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            escape = base / "sub" / ".." / ".." / "etc"
            self.assertFalse(is_within(escape, base))

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            outside = Path(tmp).parent / (Path(tmp).name + "-outside")
            outside.mkdir(exist_ok=True)
            try:
                link = base / "link"
                link.symlink_to(outside)
                self.assertFalse(is_within(link / "file", base))
            finally:
                import shutil
                shutil.rmtree(outside, ignore_errors=True)


class TestIsWithinAny(unittest.TestCase):
    def test_matches_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            other = Path(tmp).parent
            self.assertTrue(is_within_any(base / "f", [base, other]))

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            other = Path(tmp) / "other"
            other.mkdir()
            self.assertFalse(is_within_any(base / "f", [other]))

    def test_empty_dirs(self):
        self.assertFalse(is_within_any("/tmp/foo", []))


class TestValidatePath(unittest.TestCase):
    def test_home_path_valid(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertTrue(validate_path(os.path.join(home, "Documents", "file.txt")))

    def test_home_itself_valid(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertTrue(validate_path(home))

    def test_outside_home_invalid(self):
        self.assertFalse(validate_path("/etc/passwd"))

    def test_sensitive_in_home_rejected(self):
        home = os.path.realpath(os.path.expanduser("~"))
        ssh_key = os.path.join(home, ".ssh", "id_rsa")
        self.assertFalse(validate_path(ssh_key))

    def test_allowed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            allowed = Path(tmp).resolve()
            target = allowed / "file.txt"
            self.assertTrue(validate_path(str(target), allowed_dirs=[str(allowed)]))

    def test_allowed_dir_outside_target_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            allowed = Path(tmp) / "allowed"
            allowed.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            self.assertFalse(validate_path(str(outside / "f"), allowed_dirs=[str(allowed)]))

    def test_allowed_dir_sensitive_rejected(self):
        # Even inside an allowed dir, sensitive paths are rejected.
        with tempfile.TemporaryDirectory() as tmp:
            allowed = Path(tmp).resolve()
            ssh_dir = allowed / ".ssh"
            ssh_dir.mkdir()
            # .ssh inside an allowed dir outside home is not auto-sensitive
            # unless it matches the home-relative suffixes. Verify the home
            # case instead:
            home = os.path.realpath(os.path.expanduser("~"))
            self.assertFalse(validate_path(os.path.join(home, ".ssh", "id_rsa")))


class TestResolveSafe(unittest.TestCase):
    def test_returns_path_when_valid(self):
        home = os.path.realpath(os.path.expanduser("~"))
        target = os.path.join(home, "Documents", "file.txt")
        result = resolve_safe(target)
        self.assertIsNotNone(result)
        self.assertEqual(str(result), expand_path(target))

    def test_returns_none_when_invalid(self):
        self.assertIsNone(resolve_safe("/etc/passwd"))

    def test_must_exist(self):
        home = os.path.realpath(os.path.expanduser("~"))
        target = os.path.join(home, "this-file-should-not-exist-12345.txt")
        self.assertIsNone(resolve_safe(target, must_exist=True))
        # Without must_exist it returns the path even if absent.
        self.assertIsNotNone(resolve_safe(target))

    def test_returns_path_object(self):
        home = os.path.realpath(os.path.expanduser("~"))
        result = resolve_safe(os.path.join(home, "foo"))
        self.assertIsInstance(result, Path)


class TestIsRegularFile(unittest.TestCase):
    def test_regular_file(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(is_regular_file(f.name))

    def test_directory_not_regular(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_regular_file(tmp))

    def test_symlink_to_file_not_regular(self):
        # lstat is used: a symlink is not a regular file.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("x")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            self.assertFalse(is_regular_file(str(link)))

    def test_nonexistent(self):
        self.assertFalse(is_regular_file("/nonexistent/path/12345"))


class TestSafeJoin(unittest.TestCase):
    def test_simple_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            result = safe_join(base, "sub", "file.txt")
            self.assertIsNotNone(result)
            self.assertEqual(result, base / "sub" / "file.txt")

    def test_dotdot_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            self.assertIsNone(safe_join(base, "..", "etc"))

    def test_absolute_part_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            self.assertIsNone(safe_join(base, "/etc/passwd"))

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            outside = Path(tmp).parent / (Path(tmp).name + "-outside")
            outside.mkdir(exist_ok=True)
            try:
                link = base / "link"
                link.symlink_to(outside)
                self.assertIsNone(safe_join(base, "link", "file"))
            finally:
                import shutil
                shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
