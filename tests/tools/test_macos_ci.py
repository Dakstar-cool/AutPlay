"""A green CI report must include executed native acceptance and every JVM suite."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("macos_ci", Path(__file__).resolve().parents[2] / "scripts/macos_ci.py")
assert SPEC and SPEC.loader
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


class MacEvidenceTests(unittest.TestCase):
    def test_required_suites_and_native_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in CI.REPORTS.values():
                (root / directory).mkdir(parents=True)
                (root / directory / "TEST-policy.xml").write_text(
                    '<testsuite><testcase classname="Policy" name="policy"/></testsuite>', encoding="utf-8")
            native = root / CI.REPORTS["desktop"] / "TEST-native.xml"
            counts, problems = CI.test_reports(root)
            self.assertIn("Packaged AVFoundation acceptance did not pass", problems)
            for marker in ("<skipped/>", "<failure/>", "<error/>", ""):
                with self.subTest(marker=marker):
                    native.write_text(f'<testsuite><testcase classname="{CI.NATIVE_CLASS}" '
                                      f'name="{CI.NATIVE_CASE}">{marker}</testcase></testsuite>', encoding="utf-8")
                    counts, problems = CI.test_reports(root)
                    self.assertEqual(not problems, marker == "")
            self.assertEqual(counts["desktop"]["tests"], 2)
            (root / CI.REPORTS["shared/ui"] / "TEST-policy.xml").unlink()
            self.assertIn("shared/ui: missing, failed or skipped tests", CI.test_reports(root)[1])
            native.write_text("<broken", encoding="utf-8")
            self.assertIn("desktop: malformed JUnit report", CI.test_reports(root)[1])


if __name__ == "__main__":
    unittest.main()
