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
    
    def test_empty_directories(self):
        """Test diagnostics on empty directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            diag = ConfigDiagnostics(tmpdir, tmpdir)
            report = diag.run_all_checks()
            
            # Should have warnings about missing directories
            assert report.has_warnings()
    
    def test_valid_configurations(self):
        """Test diagnostics with valid configurations."""
        with tempfile.TemporaryDirectory() as pos_tmpdir:
            with tempfile.TemporaryDirectory() as agent_tmpdir:
                # Create a valid position file
                pos_file = Path(pos_tmpdir) / "developer.yaml"
                pos_file.write_text("""
position:
  type: developer
  title: 开发工程师
  jd: 负责代码编写
  department: 技术部
  proactive: false
  rules:
    - 规则1
  default_skills:
    - skill1
""")
                
                # Create a valid agent file
                agent_file = Path(agent_tmpdir) / "dev-001.yaml"
                agent_file.write_text("""
agent:
  id: dev-001
  name: 测试开发
  personality: 测试性格
  position_id: developer
  llm:
    model_name: gpt-4
    temperature: 0.7
  docker:
    image: ubuntu:22.04
    memory_limit: 512m
  skills:
    - file-operations
""")
                
                diag = ConfigDiagnostics(pos_tmpdir, agent_tmpdir)
                report = diag.run_all_checks()
                
                # Should have no errors
                assert not report.has_errors()
    
    def test_invalid_yaml(self):
        """Test diagnostics with invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create invalid YAML
            bad_file = Path(tmpdir) / "bad.yaml"
            bad_file.write_text("invalid: yaml: [")
            
            diag = ConfigDiagnostics(tmpdir, tmpdir)
            report = diag.run_all_checks()
            
            # Should have errors
            assert report.has_errors() or len(report.issues) > 0
    
    def test_missing_required_fields(self):
        """Test diagnostics with missing required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create position missing required fields
            pos_file = Path(tmpdir) / "incomplete.yaml"
            pos_file.write_text("""
position:
  type: incomplete
  # missing title and jd
""")
            
            diag = ConfigDiagnostics(tmpdir, tmpdir)
            report = diag.validate_positions()
            
            # Should have errors about missing fields
            errors = report.get_errors()
            assert any("title" in e.message or "jd" in e.message for e in errors)
    
    def test_invalid_position_reference(self):
        """Test diagnostics detecting invalid position references."""
        with tempfile.TemporaryDirectory() as pos_tmpdir:
            with tempfile.TemporaryDirectory() as agent_tmpdir:
                # Create agent referencing non-existent position
                agent_file = Path(agent_tmpdir) / "bad-agent.yaml"
                agent_file.write_text("""
agent:
  id: bad-agent
  name: Bad Agent
  position_id: nonexistent_position
""")
                
                diag = ConfigDiagnostics(pos_tmpdir, agent_tmpdir)
                report = diag.check_references()
                
                # Should have errors about invalid reference
                errors = report.get_errors()
                assert any("nonexistent" in e.message for e in errors)
    
    def test_orphaned_position(self):
        """Test diagnostics detecting orphaned positions."""
        with tempfile.TemporaryDirectory() as pos_tmpdir:
            with tempfile.TemporaryDirectory() as agent_tmpdir:
                # Create position with no agents
                pos_file = Path(pos_tmpdir) / "orphan.yaml"
                pos_file.write_text("""
position:
  type: orphan
  title: 孤儿岗位
  jd: 没有被任何Agent引用
""")
                
                diag = ConfigDiagnostics(pos_tmpdir, agent_tmpdir)
                report = diag.check_references()
                
                # Should have info about orphaned position
                infos = [i for i in report.issues if i.severity == IssueSeverity.INFO]
                assert any("orphan" in i.message.lower() or "没有" in i.message for i in infos)
    
    def test_get_position_usage_stats(self):
        """Test getting position usage statistics."""
        with tempfile.TemporaryDirectory() as pos_tmpdir:
            with tempfile.TemporaryDirectory() as agent_tmpdir:
                # Create agents
                agent1 = Path(agent_tmpdir) / "agent1.yaml"
                agent1.write_text("""
agent:
  id: agent1
  name: Agent 1
  position_id: developer
""")
                
                agent2 = Path(agent_tmpdir) / "agent2.yaml"
                agent2.write_text("""
agent:
  id: agent2
  name: Agent 2
  position_id: developer
""")
                
                agent3 = Path(agent_tmpdir) / "agent3.yaml"
                agent3.write_text("""
agent:
  id: agent3
  name: Agent 3
  position_id: operator
""")
                
                diag = ConfigDiagnostics(pos_tmpdir, agent_tmpdir)
                stats = diag.get_position_usage_stats()
                
                assert stats.get("developer") == 2
                assert stats.get("operator") == 1


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
