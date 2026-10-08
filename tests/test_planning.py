"""规划脚本的范围完整性、段隔离与 CLI 行为验证；全部产物在临时目录。"""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "书籍"
        (self.base / "原文").mkdir(parents=True)
        self.index = self.base / "chapters.tsv"
        self.rows = []
        for idx in range(125, 145):
            title = f"第{idx - 124}章 卷内章名{idx}"
            source = self.base / "原文" / f"第{idx:04d}章.txt"
            source.write_text(title + "\n原文内容\n", encoding="utf-8")
            self.rows.append([idx, title, (idx % 3 + 1) * 100,
                              str(source.relative_to(self.base))])
        self.write_index(self.rows)

    def write_index(self, rows):
        table = io.StringIO(newline="")
        writer = csv.writer(table, delimiter="\t", lineterminator="\n")
        writer.writerow(["idx", "title", "chars", "file"])
        writer.writerows(rows)
        self.index.write_text(table.getvalue(), encoding="utf-8-sig")

    def run_cli(self, script, *args, code=0):
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script),
                                 "--index", str(self.index), "--base", str(self.base),
                                 *map(str, args)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_segments_cover_scope_and_preserve_index_titles(self):
        data = json.loads(self.run_cli("make_segments.py", "--chapters", 8, "--json").stdout)
        segments = data["segments"]
        self.assertEqual([(s["start"], s["end"]) for s in segments],
                         [(125, 132), (133, 140), (141, 144)])
        self.assertEqual(sum(s["chars"] for s in segments), sum(r[2] for r in self.rows))
        self.assertEqual(segments[0]["first_title"], self.rows[0][1])
        self.assertEqual([s["next_start"] for s in segments], [133, 141, None])
        with Path(data["manifest"]).open(encoding="utf-8", newline="") as handle:
            records = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(sum(int(r["章数"]) for r in records), 20)
        self.assertIn("尚未核对剧情", Path(data["markdown"]).read_text(encoding="utf-8"))
        # 草案不能覆盖手填的断点依据。
        original = Path(data["markdown"]).read_bytes()
        self.run_cli("make_segments.py", "--chapters", 10, code=2)
        self.assertEqual(Path(data["markdown"]).read_bytes(), original)

    def test_explicit_ends_and_character_budget(self):
        data = json.loads(self.run_cli("make_segments.py", "--ends", "130,144",
                                      "--first-number", 4, "--dry-run", "--json").stdout)
        self.assertEqual([(s["segment"], s["start"], s["end"]) for s in data["segments"]],
                         [("段04", 125, 130), ("段05", 131, 144)])
        budget = json.loads(self.run_cli("make_segments.py", "--target-chars", 600,
                                        "--dry-run", "--json").stdout)["segments"]
        for s in budget[:-1]:
            self.assertGreaterEqual(s["chars"], 600)
            before_last = sum(r[2] for r in self.rows if s["start"] <= r[0] < s["end"])
            self.assertLess(before_last, 600)
        self.assertFalse((self.base / "计划").exists())

    def test_segment_batches_are_local_and_isolated(self):
        first = json.loads(self.run_cli("make_plan.py", "--start", 125, "--end", 140,
                                      "--segment", "第125-140章", "--spec", "计划/规范.md",
                                      "--json").stdout)
        original = Path(first["batches"][0]["plan_file"]).read_bytes()
        second = json.loads(self.run_cli("make_plan.py", "--start", 141, "--end", 144,
                                       "--segment", "第141-144章", "--json").stdout)
        self.assertEqual([b["batch"] for b in first["batches"]], [1, 2])
        self.assertEqual([(b["start"], b["end"]) for b in first["batches"]], [(125, 136), (137, 140)])
        self.assertEqual(second["batches"][0]["batch"], 1)
        self.assertNotEqual(first["manifest"], second["manifest"])
        self.assertEqual(Path(first["batches"][0]["plan_file"]).read_bytes(), original)
        task = original.decode("utf-8")
        self.assertIn(str(self.base / "第125-140章" / "分章卡片"), task)
        self.assertIn(str(self.base / "计划" / "规范.md"), task)
        self.assertIn(str((self.base / self.rows[0][3]).resolve()), task)
        self.assertIn(self.rows[0][1], task)
        self.assertIn("idx 125", task)

    def test_global_grid_is_preserved_and_dry_run_writes_nothing(self):
        data = json.loads(self.run_cli("make_plan.py", "--dry-run", "--json").stdout)
        self.assertEqual([(b["batch"], b["start"], b["end"]) for b in data["batches"]],
                         [(11, 125, 132), (12, 133, 144)])
        global_spec = json.loads(self.run_cli("make_plan.py", "--spec", "规范.md",
                                             "--dry-run", "--json").stdout)
        self.assertEqual(global_spec["spec"], str((Path.cwd() / "规范.md").resolve()))
        self.run_cli("make_plan.py", "--segment", "预览", "--dry-run")
        self.assertFalse((self.base / "计划").exists())
        self.assertFalse((self.base / "预览").exists())

    def test_bad_ranges_and_indices_fail_before_writing(self):
        for rows in (self.rows[1:], self.rows[:5] + self.rows[4:]):
            self.write_index(rows)
            for script in ("make_plan.py", "make_segments.py"):
                self.run_cli(script, "--start", 125, "--end", 144, code=2)
            self.assertFalse((self.base / "计划").exists())
        self.write_index(self.rows)
        for ends in ("130,143", "140,130,144", "124,144", "abc", ""):
            self.run_cli("make_segments.py", "--ends", ends, code=2)
        self.run_cli("make_plan.py", "--start", 144, "--end", 125, code=2)
        self.run_cli("make_plan.py", "--segment", "../其他书", code=2)
        self.assertFalse((self.base / "计划").exists())

    def test_missing_source_returns_failure_without_dry_run_output(self):
        (self.base / self.rows[0][3]).unlink()
        result = self.run_cli("make_plan.py", "--segment", "预览", "--dry-run", "--json", code=1)
        self.assertEqual(len(json.loads(result.stdout)["missing_files"]), 1)
        self.assertFalse((self.base / "计划").exists())


if __name__ == "__main__":
    unittest.main()
