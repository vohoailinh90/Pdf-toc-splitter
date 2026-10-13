"""check_cli_contract - guards cli-surface, password-handling, docs-and-examples, packaging.

The command line is the interface people script against. Whether the README
and the argparse definition agree, what the exit codes are, and that a PDF
password can only ever come from a prompt are all computable.
"""
import ast
import importlib
import os
import re
import subprocess
import sys
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf_toc_splitter.__main__ import main
from tests.support.pdfs import build_encrypted_pdf, build_pdf, build_text_pdf, tree, widths
from tests.support.yamlcompat import require_dependency

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pdf_toc_splitter"
SECRET_WORDS = ("password", "passwd", "secret", "token", "apikey", "api-key")
ALLOWED_SECRET_FLAGS = {"--ask-password"}


def run(*args, cwd=ROOT, env=None):
    return subprocess.run([sys.executable, "-m", "pdf_toc_splitter", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=60,
                          env={**os.environ, **env} if env else None)


def help_text():
    return run("--help").stdout


def declared_flags():
    return set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", help_text())) - {"--help"}


def documented_flags():
    """Flags the README presents for this tool: inline spans that start with a flag or name
    the tool, and `pdf-toc-splitter ...` command lines. The flags of other programs
    (`route.py --record`, `gh repo create --private`) are not this tool's contract."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    found = set()
    for span in re.findall(r"`([^`\n]+)`", readme):
        if span.startswith("--") or "pdf-toc-splitter" in span:
            found.update(re.findall(r"(?<![\w-])--[a-z][a-z-]*", span))
    for line in readme.splitlines():
        if line.strip().startswith("pdf-toc-splitter "):
            found.update(re.findall(r"(?<![\w-])--[a-z][a-z-]*", line))
    return found


class FlagsAndReadmeAgree(unittest.TestCase):
    def test_every_declared_flag_is_documented(self):
        self.assertEqual(declared_flags() - documented_flags(), set())

    def test_every_documented_flag_exists(self):
        self.assertEqual(documented_flags() - declared_flags(), set())

    def test_the_expected_surface_is_exactly_this(self):
        self.assertEqual(declared_flags(), {
            "--toc-file", "--toc-pages", "--bookmark-level", "--page-offset",
            "--output", "--dry-run", "--ask-password",
        })


class PasswordOnlyComesFromAPrompt(unittest.TestCase):
    def test_no_flag_takes_a_secret_value(self):
        for flag in declared_flags():
            if any(word in flag for word in SECRET_WORDS):
                with self.subTest(flag=flag):
                    self.assertIn(flag, ALLOWED_SECRET_FLAGS)
                    self.assertIsNone(re.search(re.escape(flag) + r"\s+[A-Z_]{2,}", help_text()),
                                      f"{flag} appears to take a value")

    def test_the_sources_never_read_or_print_the_password_elsewhere(self):
        for path in sorted(PACKAGE.glob("*.py")):
            tree_ = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree_):
                with self.subTest(file=path.name, line=getattr(node, "lineno", 0)):
                    if isinstance(node, ast.Attribute) and node.attr == "password" and isinstance(node.value, ast.Name) and node.value.id == "args":
                        self.fail("password read from the parsed arguments")
                    if isinstance(node, ast.Import):
                        self.assertNotIn("logging", [a.name for a in node.names])
                    if isinstance(node, (ast.Attribute, ast.Name)):
                        name = node.attr if isinstance(node, ast.Attribute) else node.id
                        self.assertNotIn(name, {"environ", "getenv"}, "the password must not come from the environment")
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                        self.assertNotIn("password", names, "the password is passed to print()")
                    if isinstance(node, ast.JoinedStr):
                        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                        self.assertNotIn("password", names, "the password is interpolated into a string")


class ExitCodesAndStreams(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = build_pdf(self.root / "doc.pdf", pages=6, outline=[("Phần A", 0, 1), ("Phần B", 3, 1)])
        self.toc = self.root / "toc.txt"
        self.toc.write_text("Mở đầu....1\nNội dung....4\n", encoding="utf-8")

    def test_help_succeeds(self):
        self.assertEqual(run("--help").returncode, 0)

    def test_dry_run_prints_the_plan_writes_nothing_and_exits_zero(self):
        result = run(self.pdf, "--toc-file", self.toc, "--dry-run", "--output", self.root / "out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 2)
        for line in result.stdout.splitlines():
            self.assertRegex(line, r"^\s*\d+-\d+\s+\S.*\.pdf$")
        self.assertFalse((self.root / "out").exists())

    def test_a_real_run_reports_the_count_and_a_rerun_fails_without_touching_files(self):
        out = self.root / "out"
        first = run(self.pdf, "--toc-file", self.toc, "--output", out)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Đã tạo 2 file", first.stdout)
        snapshot = tree(out)
        second = run(self.pdf, "--toc-file", self.toc, "--output", out)
        self.assertEqual(second.returncode, 1)
        self.assertTrue(second.stderr.startswith("Lỗi:"), second.stderr)
        self.assertEqual(tree(out), snapshot)

    def test_errors_exit_one_with_the_message_on_stderr_only(self):
        cases = [
            ("missing pdf", [self.root / "nope.pdf", "--dry-run"]),
            ("bad toc line", [self.pdf, "--toc-file", self.root / "bad.txt", "--dry-run"]),
            ("offset with bookmarks", [self.pdf, "--page-offset", "1", "--dry-run"]),
            ("page past the end", [self.pdf, "--toc-file", self.root / "far.txt", "--dry-run"]),
            ("toc pages past the end", [self.pdf, "--toc-pages", "9-10", "--dry-run"]),
            ("bookmark level 0", [self.pdf, "--bookmark-level", "0", "--dry-run"]),
        ]
        (self.root / "bad.txt").write_text("không có trang", encoding="utf-8")
        (self.root / "far.txt").write_text("X....99", encoding="utf-8")
        for label, args in cases:
            with self.subTest(label):
                result = run(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertTrue(result.stderr.startswith("Lỗi:"), result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_stream_that_cannot_encode_vietnamese_degrades_instead_of_aborting(self):
        # ascii stands for any code page that lacks the letters: output redirected on Windows.
        env = {"PYTHONIOENCODING": "ascii:strict"}
        out = self.root / "out"
        dry = run(self.pdf, "--toc-file", self.toc, "--dry-run", "--output", out, env=env)
        self.assertEqual((dry.returncode, dry.stderr), (0, ""), dry.stderr)
        self.assertEqual(len(dry.stdout.splitlines()), 2)
        real = run(self.pdf, "--toc-file", self.toc, "--output", out, env=env)
        self.assertEqual((real.returncode, real.stderr), (0, ""), real.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["Mở đầu.pdf", "Nội dung.pdf"])
        again = run(self.pdf, "--toc-file", self.toc, "--output", out, env=env)
        self.assertEqual(again.returncode, 1)
        self.assertNotIn("Traceback", again.stderr)

    def test_the_toc_sources_are_mutually_exclusive(self):
        self.assertEqual(run(self.pdf, "--toc-file", self.toc, "--toc-pages", "1").returncode, 2)

    def test_bookmarks_are_the_default_source(self):
        result = run(self.pdf, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 2)


class TocReadFromPdfPages(unittest.TestCase):
    """--toc-pages: the TOC is the text layer of physical pages 2-3, and the page numbers in it
    are the exact source pages of the exported sections (widths 100..105 identify them)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = build_text_pdf(self.root / "doc.pdf", [
            ["Cover"],
            ["Contents", "Alpha....4"],
            ["Beta....6"],
            [], [], [],
        ])

    def test_a_heading_line_on_the_toc_pages_is_rejected_not_skipped(self):
        result = run(self.pdf, "--toc-pages", "2-3", "--dry-run")
        self.assertEqual((result.returncode, result.stderr), (1, "Lỗi: Dòng mục lục 1 không hợp lệ: Contents\n"),
                         "the heading line is not a TOC line: it must be rejected, not skipped")
        self.assertEqual(result.stdout, "")

    def test_a_clean_toc_page_range_exports_the_exact_pages(self):
        self.pdf = build_text_pdf(self.root / "clean.pdf", [["Cover"], ["Alpha....4"], ["Beta....6"], [], [], []])
        out = self.root / "out"
        dry = run(self.pdf, "--toc-pages", "2-3", "--dry-run", "--output", out)
        self.assertEqual((dry.returncode, dry.stderr), (0, ""), dry.stderr)
        self.assertEqual([line.split() for line in dry.stdout.splitlines()],
                         [["4-5", "Alpha.pdf"], ["6-6", "Beta.pdf"]])
        self.assertFalse(out.exists())
        real = run(self.pdf, "--toc-pages", "2-3", "--output", out)
        self.assertEqual((real.returncode, real.stderr), (0, ""), real.stderr)
        self.assertEqual(widths(out / "Alpha.pdf"), [103, 104])
        self.assertEqual(widths(out / "Beta.pdf"), [105])
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["Alpha.pdf", "Beta.pdf"])

    def test_only_the_named_pages_are_read(self):
        self.pdf = build_text_pdf(self.root / "clean.pdf", [["Cover"], ["Alpha....4"], ["Beta....6"], [], [], []])
        one = run(self.pdf, "--toc-pages", "2", "--dry-run")
        self.assertEqual((one.returncode, one.stderr), (0, ""), one.stderr)
        self.assertEqual([line.split() for line in one.stdout.splitlines()], [["4-6", "Alpha.pdf"]])
        cover = run(self.pdf, "--toc-pages", "1", "--dry-run")
        self.assertEqual(cover.returncode, 1)
        self.assertTrue(cover.stderr.startswith("Lỗi:"), cover.stderr)
        self.assertEqual(cover.stdout, "")

    def test_pages_without_a_text_layer_are_an_error_not_an_empty_plan(self):
        result = run(self.pdf, "--toc-pages", "4-5", "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Lỗi:"), result.stderr)
        self.assertEqual(result.stdout, "")


class EncryptedPdf(unittest.TestCase):
    """The password path, exercised rather than only scanned: it is prompted for
    only when --ask-password is given, a wrong or missing one is an error, and
    nothing about it leaks onto either stream."""

    PASSWORD = "s3cret-Mật-khẩu"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        require_dependency("cryptography", "AES-encrypted test PDFs")
        self.pdf = build_encrypted_pdf(Path(self.temp.name) / "locked.pdf", self.PASSWORD)

    def run_main(self, *args, typed=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["pdf-toc-splitter", str(self.pdf), "--dry-run", *args]), \
                mock.patch("getpass.getpass", return_value=typed) as prompt, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main()
        return code, out.getvalue(), err.getvalue(), prompt

    def test_without_the_flag_it_never_prompts_and_tells_the_user_what_to_do(self):
        code, out, err, prompt = self.run_main(typed=self.PASSWORD)
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        self.assertTrue(err.startswith("Lỗi:") and "--ask-password" in err, err)
        self.assertEqual(out, "")

    def test_with_the_flag_the_right_password_unlocks_it(self):
        code, out, err, prompt = self.run_main("--ask-password", typed=self.PASSWORD)
        self.assertEqual((code, err), (0, ""), err)
        prompt.assert_called_once()
        self.assertRegex(out, r"^\s*1-3\s+Phần A\.pdf")

    def test_with_the_flag_a_wrong_password_is_an_error(self):
        code, out, err, prompt = self.run_main("--ask-password", typed="wrong")
        self.assertEqual(code, 1)
        prompt.assert_called_once()
        self.assertTrue(err.startswith("Lỗi:"), err)
        self.assertEqual(out, "")

    def test_the_password_never_reaches_stdout_or_stderr(self):
        for typed, flags in ((self.PASSWORD, ("--ask-password",)), ("wrong", ("--ask-password",)), (self.PASSWORD, ())):
            with self.subTest(typed=typed, flags=flags):
                _, out, err, _ = self.run_main(*flags, typed=typed)
                self.assertNotIn(typed, out + err)
                self.assertNotIn(self.PASSWORD, out + err)


class PackagingResolves(unittest.TestCase):
    def test_the_console_script_points_at_a_callable(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^pdf-toc-splitter\s*=\s*"([\w.]+):(\w+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(match, "no [project.scripts] entry for pdf-toc-splitter")
        module, attribute = match.groups()
        self.assertTrue(callable(getattr(importlib.import_module(module), attribute)))

    def test_the_declared_python_floor_matches_the_ci_floor(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.10"', pyproject)
        ci = ROOT / ".github" / "workflows" / "ci.yml"
        if ci.is_file():
            self.assertIn('"3.10"', ci.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
