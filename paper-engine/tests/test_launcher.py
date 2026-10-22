import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fixtures import T
import run


class LauncherTests(unittest.TestCase):
    def test_inherited_freqtrade_credentials_and_live_override_removed(self):
        env = run.child_env(Path("/tmp/synthetic"), {"FREQTRADE__DRY_RUN": "false",
            "FREQTRADE__EXCHANGE__PRIVATEKEY": "SYNTHETIC_NOT_A_KEY", "HTTPS_PROXY": "http://example.org"})
        self.assertFalse(any(k.startswith("FREQTRADE__") for k in env))
        self.assertIn("HTTPS_PROXY", env)

    def test_no_account_credentials_rpc_or_live_flags(self):
        cfg = run.validated_configuration(Path("/tmp/synthetic"))
        self.assertIs(cfg["dry_run"], True)
        self.assertEqual(cfg["max_open_trades"], 1)
        self.assertEqual(cfg["exchange"]["privateKey"], "")
        self.assertEqual(cfg["exchange"]["walletAddress"], "")
        self.assertFalse(cfg["api_server"]["enabled"])
        self.assertFalse(cfg["force_entry_enable"])
        self.assertIn("freqtrade.sqlite", cfg["db_url"])
        self.assertNotIn("radar.sqlite", cfg["db_url"])

    def test_second_process_lock_is_refused_and_released(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with run.singleton(root):
                with self.assertRaisesRegex(RuntimeError, "already uses"):
                    with run.singleton(root): pass
            with run.singleton(root): pass

    def test_unknown_cli_options_cannot_enable_live_mode(self):
        with self.assertRaises(SystemExit): run.main(["trade", "--live"])

    def test_missing_webhook_never_enqueues_test(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing"):
                run.main(["test-slack", "--data", temp])
            self.assertFalse((Path(temp)/"radar.sqlite").exists())


if __name__ == "__main__": unittest.main()
