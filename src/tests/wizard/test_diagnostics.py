"""Tests for ConfigDiagnostics."""

import tempfile
from pathlib import Path

from tlon.app.wizard.diagnostics import (
    ConfigDiagnostics,
    DiagnosticReport,
    IssueSeverity,
)


class TestConfigDiagnostics:
    """Test cases for ConfigDiagnostics."""

    def test_empty_directory(self):
        """Test diagnostics on empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            diag = ConfigDiagnostics(tmpdir)
            report = diag.run_all_checks()

            # Should have warnings about no agent files
            assert report.has_warnings()

    def test_valid_agent_configuration(self):
        """Test diagnostics with valid agent configuration."""
        with tempfile.TemporaryDirectory() as agent_tmpdir:
            # Create a valid agent file
            agent_file = Path(agent_tmpdir) / "dev-001.yaml"
            agent_file.write_text("""
agent:
  id: dev-001
  name: 测试开发
  personality: 测试性格
  llm:
    model_name: gpt-4
    temperature: 0.7
  docker:
    image: ubuntu:22.04
    memory_limit: 512m
""")

            diag = ConfigDiagnostics(agent_tmpdir)
            report = diag.run_all_checks()

            # Should have no errors
            assert not report.has_errors()

    def test_invalid_yaml(self):
        """Test diagnostics with invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create invalid YAML
            bad_file = Path(tmpdir) / "bad.yaml"
            bad_file.write_text("invalid: yaml: [")

            diag = ConfigDiagnostics(tmpdir)
            report = diag.run_all_checks()

            # Should have errors
            assert report.has_errors() or len(report.issues) > 0

    def test_missing_required_fields(self):
        """Test diagnostics with missing required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create agent missing required fields
            agent_file = Path(tmpdir) / "incomplete.yaml"
            agent_file.write_text("""
agent:
  personality: test
  # missing id and name
""")

            diag = ConfigDiagnostics(tmpdir)
            report = diag.validate_agents()

            # Should have errors about missing fields
            errors = report.get_errors()
            assert any("id" in e.message or "name" in e.message for e in errors)


class TestDiagnosticReport:
    """Test cases for DiagnosticReport."""

    def test_add_issue(self):
        """Test adding issues to report."""
        report = DiagnosticReport()

        report.add(IssueSeverity.ERROR, "test", "Test error")
        report.add(IssueSeverity.WARNING, "test", "Test warning")
        report.add(IssueSeverity.INFO, "test", "Test info")

        assert len(report.issues) == 3
        assert report.has_errors()
        assert report.has_warnings()

    def test_get_errors(self):
        """Test getting only errors."""
        report = DiagnosticReport()

        report.add(IssueSeverity.ERROR, "test", "Error 1")
        report.add(IssueSeverity.ERROR, "test", "Error 2")
        report.add(IssueSeverity.WARNING, "test", "Warning 1")

        errors = report.get_errors()
        assert len(errors) == 2

    def test_get_summary(self):
        """Test getting summary."""
        report = DiagnosticReport()

        assert report.get_summary() == "All checks passed"

        report.add(IssueSeverity.ERROR, "test", "Error")
        report.add(IssueSeverity.WARNING, "test", "Warning")

        summary = report.get_summary()
        assert "1 error" in summary
        assert "1 warning" in summary
