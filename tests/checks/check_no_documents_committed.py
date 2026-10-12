"""check_no_documents_committed - runs on every change (always_runs).

This tool is pointed at people's own documents, so the realistic leak is a
test run or a demo that leaves a PDF (or its split output) in the working tree
and a careless `git add -A` that commits it. Fixtures are built on the fly by
tests/support/pdfs.py; nothing binary belongs in the repository.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_SUFFIXES = (".pdf",)
MUST_BE_IGNORED = ["a.pdf", "dir/b.PDF", "split-output/x.txt", ".venv/x", "__pycache__/x.pyc", ".claude/settings.local.json"]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


class NoDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None or git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            raise unittest.SkipTest("not a git checkout (an sdist, for example): nothing is tracked here")

    def test_no_tracked_file_is_a_document(self):
        tracked = git("ls-files", "-z").stdout.split("\0")
        documents = [p for p in tracked if p.lower().endswith(DOCUMENT_SUFFIXES) or p.startswith("split-output/")]
        self.assertEqual(documents, [])

    def test_the_gitignore_keeps_documents_and_local_state_out(self):
        for path in MUST_BE_IGNORED:
            with self.subTest(path=path):
                self.assertEqual(git("check-ignore", "-q", "--no-index", path).returncode, 0, f"{path} is not ignored")


if __name__ == "__main__":
    unittest.main()
