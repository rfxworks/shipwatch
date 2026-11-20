import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import deployer


class DeployerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "observer.py"
        self.target.write_bytes(b"print('working')\n")
        self.state = self.root / "state"
        self.target_patch = patch.object(deployer, "TARGET", self.target)
        self.state_patch = patch.object(deployer, "STATE", self.state)
        self.target_patch.start()
        self.state_patch.start()

    def tearDown(self):
        self.target_patch.stop()
        self.state_patch.stop()
        self.temp.cleanup()

    def test_mismatched_artifact_is_rejected_before_install(self):
        release = {"project": "shipwatch", "release": "1.0.1", "backend_sha256": "a" * 64}
        old = self.target.read_bytes()
        with patch.object(deployer, "read", side_effect=[json.dumps(release).encode(), b"wrong artifact"]), patch.object(deployer.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "did not match"):
                deployer.main()
        self.assertEqual(self.target.read_bytes(), old)
        run.assert_not_called()

    def test_read_forwards_the_explicit_loopback_timeout(self):
        response = MagicMock(status=200, url="http://127.0.0.1:8789/health")
        response.read.return_value = b'{"status":"ok"}'
        opened = MagicMock()
        opened.__enter__.return_value = response
        with patch.object(deployer, "urlopen", return_value=opened) as urlopen:
            deployer.read(response.url, timeout=0.75)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 0.75)

    def test_unresponsive_health_checks_exhaust_a_five_second_budget(self):
        elapsed = [0.0]
        timeouts = []
        def read(url, timeout):
            timeouts.append(timeout)
            elapsed[0] += timeout
            raise TimeoutError("loopback stalled")
        def sleep(seconds):
            elapsed[0] += seconds
        with patch.object(deployer.time, "monotonic", side_effect=lambda: elapsed[0]), patch.object(deployer.time, "sleep", side_effect=sleep), patch.object(deployer, "read", side_effect=read):
            self.assertFalse(deployer.healthy("a" * 64))
        self.assertTrue(timeouts)
        self.assertLessEqual(max(timeouts), 1)
        self.assertAlmostEqual(elapsed[0], 5)

    def test_slow_release_verification_aborts_before_touching_the_installed_observer(self):
        raw = b"print('candidate')\n"
        digest = hashlib.sha256(raw).hexdigest()
        release = {"project": "shipwatch", "release": "1.0.1", "backend_sha256": digest}
        old = self.target.read_bytes()
        with patch.object(deployer, "read", side_effect=[json.dumps(release).encode(), raw]), patch.object(deployer.time, "monotonic", side_effect=[0, 16]), patch.object(deployer.subprocess, "run") as run:
            with self.assertRaisesRegex(TimeoutError, "safe rollout time budget"):
                deployer.main()
        self.assertEqual(self.target.read_bytes(), old)
        run.assert_not_called()

    def test_failed_health_rolls_back_and_does_not_repeat_deployment(self):
        raw = b"print('candidate')\n"
        digest = hashlib.sha256(raw).hexdigest()
        release = {"project": "shipwatch", "release": "1.0.1", "backend_sha256": digest}
        old = self.target.read_bytes()
        with patch.object(deployer, "read", side_effect=[json.dumps(release).encode(), raw]), patch.object(deployer, "healthy", side_effect=[False, True]), patch.object(deployer.subprocess, "run") as run, patch.object(deployer, "notify") as notify:
            deployer.main()
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(run.call_count, 2)
        notify.assert_called_once_with(digest)
        with patch.object(deployer, "read", return_value=json.dumps(release).encode()), patch.object(deployer.subprocess, "run") as retry:
            deployer.main()
        retry.assert_not_called()
        status = json.loads((self.state / "status.json").read_text())
        self.assertEqual(status["failed_digest"], digest)
        self.assertEqual(status["active_digest"], hashlib.sha256(old).hexdigest())
        self.assertTrue(status["rollback_verified"])

    def test_restart_error_or_timeout_restores_and_verifies_previous_observer(self):
        raw = b"print('candidate')\n"
        digest = hashlib.sha256(raw).hexdigest()
        release = {"project": "shipwatch", "release": "1.0.1", "backend_sha256": digest}
        old = self.target.read_bytes()
        for failure in (subprocess.CalledProcessError(1, "systemctl"), subprocess.TimeoutExpired("systemctl", 25)):
            with self.subTest(failure=type(failure).__name__):
                (self.state / "status.json").unlink(missing_ok=True)
                with patch.object(deployer, "read", side_effect=[json.dumps(release).encode(), raw]), patch.object(deployer.subprocess, "run", side_effect=[failure, None]) as run, patch.object(deployer, "healthy", return_value=True) as healthy, patch.object(deployer, "notify") as notify:
                    deployer.main()
                self.assertEqual(self.target.read_bytes(), old)
                self.assertEqual(run.call_count, 2)
                healthy.assert_called_once_with(hashlib.sha256(old).hexdigest())
                notify.assert_called_once_with(digest)
                status = json.loads((self.state / "status.json").read_text())
                self.assertEqual(status["status"], "rolled_back")
                self.assertEqual(status["candidate_error"], type(failure).__name__)

    def test_failed_rollback_is_recorded_without_claiming_an_active_observer(self):
        raw = b"print('candidate')\n"
        digest = hashlib.sha256(raw).hexdigest()
        release = {"project": "shipwatch", "release": "1.0.1", "backend_sha256": digest}
        old = self.target.read_bytes()
        with patch.object(deployer, "read", side_effect=[json.dumps(release).encode(), raw]), patch.object(deployer.subprocess, "run", side_effect=[None, subprocess.CalledProcessError(1, "systemctl")]), patch.object(deployer, "healthy", return_value=False), patch.object(deployer, "notify", side_effect=OSError("notification unavailable")):
            with self.assertRaisesRegex(OSError, "notification unavailable"):
                deployer.main()
        self.assertEqual(self.target.read_bytes(), old)
        status = json.loads((self.state / "status.json").read_text())
        self.assertEqual(status["status"], "rollback_failed")
        self.assertEqual(status["installed_digest"], hashlib.sha256(old).hexdigest())
        self.assertIsNone(status["active_digest"])
        self.assertFalse(status["rollback_verified"])
        self.assertTrue(status["notice_pending"])
        # The next timer run retries the durable notice, without reinstalling
        # the rejected candidate, and leaves the recovery failure visible.
        with patch.object(deployer, "read", return_value=json.dumps(release).encode()), patch.object(deployer.subprocess, "run") as run, patch.object(deployer, "notify") as notify:
            deployer.main()
        run.assert_not_called()
        notify.assert_called_once_with(digest)
        status = json.loads((self.state / "status.json").read_text())
        self.assertEqual(status["status"], "rollback_failed")
        self.assertFalse(status["notice_pending"])


if __name__ == "__main__":
    unittest.main()
