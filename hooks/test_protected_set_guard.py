"""Tests for protected_set_guard.py. Run: python3 -m unittest -q test_protected_set_guard (from this dir)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protected_set_guard as sg  # noqa: E402

FAKE_LSBLK = {"blockdevices": [
    {"name": "/dev/nvme0n1", "mountpoint": None, "type": "disk", "children": [
        {"name": "/dev/nvme0n1p1", "mountpoint": "/boot/efi", "type": "part"},
        {"name": "/dev/nvme0n1p2", "mountpoint": "/", "type": "part"}]},
    {"name": "/dev/nvme1n1", "mountpoint": None, "type": "disk", "children": [
        {"name": "/dev/nvme1n1p1", "mountpoint": "/mnt/scratch", "type": "part"}]},
    {"name": "/dev/sdb", "mountpoint": None, "type": "disk", "children": [
        {"name": "/dev/sdb1", "mountpoint": "/mnt/usb", "type": "part"}]},
]}


H = "/home/alice"
sg.HOME = H


class Base(unittest.TestCase):
    def setUp(self):
        sg.LSBLK_OVERRIDE = FAKE_LSBLK
        sg.configure(protected=[H + "/vault", H + "/vault-copy.bundle", H + "/vms/golden.qcow2", H + "/repo.git/.git",
                                H + "/ledger", H + "/.claude/projects", H + "/.ssh", H + "/.claude/.credentials.json"],
                     names=["golden.qcow2"], append_only=["ledger"])

    def tearDown(self):
        sg.LSBLK_OVERRIDE = None

    def assertBlocked(self, cmd, cwd=H):
        self.assertIsNotNone(sg.decide(cmd, cwd), f"expected BLOCK: {cmd}")

    def assertAllowed(self, cmd, cwd=H):
        self.assertIsNone(sg.decide(cmd, cwd), f"expected ALLOW: {cmd}")


class Blocked(Base):
    def test_rm_vault(self):
        self.assertBlocked("rm -rf /home/alice/vault")

    def test_sudo_mv_bundle_home_tilde(self):
        self.assertBlocked("sudo mv ~/vault-copy.bundle /tmp")

    def test_redirect_into_fleet_git(self):
        self.assertBlocked("cat x > /home/alice/repo.git/.git/HEAD")

    def test_wipefs_disk_holding_root(self):
        self.assertBlocked("sudo wipefs -a /dev/nvme0n1")

    def test_hook_test_path_prefix(self):
        self.assertBlocked("rm -rf /home/alice/vault-copy.bundle.nonexistent-test")

    def test_rm_glob_in_home_hits_protected(self):
        self.assertBlocked("cd /home/alice && rm -rf *")

    def test_git_clean_inside_vault(self):
        self.assertBlocked("git -C /home/alice/vault clean -fdx")

    def test_find_delete_under_archive(self):
        self.assertBlocked("find /home/alice/.claude/projects/archive -name '*.jsonl' -delete")

    def test_append_credentials(self):
        self.assertBlocked("echo hi >> /home/alice/.claude/.credentials.json")

    def test_xargs_rm_from_protected_listing(self):
        self.assertBlocked("ls /home/alice/vault | xargs rm -rf")

    def test_bash_c_wrapped(self):
        self.assertBlocked("bash -c 'rm -rf /home/alice/ledger/x'")

    def test_command_substitution(self):
        self.assertBlocked("rm $(echo /home/alice/vault)")

    def test_rm_ancestor_of_protected(self):
        self.assertBlocked("rm -rf /home/alice")

    def test_dd_to_disk_holding_root(self):
        self.assertBlocked("dd if=/dev/zero of=/dev/nvme0n1 bs=1M")

    def test_ssh_remote_rm(self):
        self.assertBlocked("ssh host1 'rm -rf /home/alice/vault'")

    def test_fail_closed_parse_error_with_protected(self):
        self.assertBlocked("rm -rf 'unterminated /home/alice/vault")


class Allowed(Base):
    def test_rm_tmp(self):
        self.assertAllowed("rm -rf /tmp/x")

    def test_ls_vault(self):
        self.assertAllowed("ls /home/alice/vault")

    def test_read_vault_copy_out(self):
        self.assertAllowed("cp /home/alice/vault/a.txt /tmp/a.txt")

    def test_redirect_to_tmp(self):
        self.assertAllowed("echo hi > /tmp/out.txt")

    def test_mkfs_label_is_not_a_path(self):
        self.assertAllowed("mkfs.ext4 -L vault /dev/nvme1n1")

    def test_mkfs_disk_without_protected_mount(self):
        self.assertAllowed("sudo mkfs.ext4 /dev/sdb1")

    def test_fdisk_readonly_listing(self):
        self.assertAllowed("fdisk -l /dev/nvme0n1")

    def test_sed_without_inplace(self):
        self.assertAllowed("sed -n 1p /home/alice/repo.git/config")

    def test_git_status_in_work(self):
        self.assertAllowed("git -C /home/alice/work status")

    def test_rm_in_work_subdir(self):
        self.assertAllowed("rm -rf /home/alice/work/tmp")

    def test_rm_tmp_glob(self):
        self.assertAllowed("rm -rf /tmp/build/*")

    def test_pipeline_without_destruction(self):
        self.assertAllowed("ls -la /home/alice && echo done | tee /tmp/a")

    def test_parse_error_without_protected_fails_open(self):
        self.assertAllowed("rm -rf 'unterminated /tmp/x")


class HookMain(unittest.TestCase):
    def run_hook(self, cmd, log):
        payload = json.dumps({"tool_input": {"command": cmd}, "cwd": "/home/alice"})
        env = dict(os.environ, SUDO_GUARD_LOG=log, HOME=H, SUDO_GUARD_PROTECTED=H + "/vault:" + H + "/vault-copy.bundle")
        return subprocess.run([sys.executable, sg.__file__], input=payload,
                              capture_output=True, text=True, env=env, timeout=10)

    def test_block_exit2_and_log_line(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "protected-set-guard.log")
            r = self.run_hook("rm -rf /home/alice/vault-copy.bundle.nonexistent-test", log)
            self.assertEqual(r.returncode, 2)
            self.assertIn("BLOCKED", r.stderr)
            with open(log) as f:
                lines = f.read().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("nonexistent-test", lines[0])

    def test_allow_exit0_no_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "protected-set-guard.log")
            r = self.run_hook("rm -rf /tmp/x", log)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(log))


if __name__ == "__main__":
    unittest.main()
