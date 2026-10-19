"""HTML 阅读进度键隔离与自定义键行为验证。"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


BUILD_HTML = Path(__file__).resolve().parents[1] / "scripts" / "build_html.py"
PAGE_DATA_RE = re.compile(
    r'<script id="page-data" type="application/json">(.*?)</script>', re.S)


class ProgressKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def build_and_read_key(self, digest, output, *args):
        digest.parent.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not digest.exists():
            digest.write_text("# 同名速读稿\n\n正文。\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(BUILD_HTML),
             "--digest", str(digest), "--out", str(output), *args],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        match = PAGE_DATA_RE.search(output.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "生成页面缺少 page-data")
        return json.loads(match.group(1))["key"]

    def test_same_named_books_get_distinct_default_keys(self):
        keys = []
        for name in ("甲书", "乙书"):
            folder = self.base / name
            keys.append(self.build_and_read_key(
                folder / "速读稿.md", folder / "速读稿.html"))
        self.assertNotEqual(keys[0], keys[1])

    def test_default_key_is_stable_when_output_name_changes(self):
        digest = self.base / "甲书" / "速读稿.md"
        first = self.build_and_read_key(digest, self.base / "甲书" / "读本.html")
        second = self.build_and_read_key(digest, self.base / "甲书" / "副本.html")
        self.assertEqual(first, second)

    def test_explicit_progress_key_is_preserved(self):
        digest = self.base / "甲书" / "速读稿.md"
        key = self.build_and_read_key(
            digest, self.base / "甲书" / "速读稿.html", "--progress-key", "甲书-第1-20章")
        self.assertEqual(key, "甲书-第1-20章")


if __name__ == "__main__":
    unittest.main()
