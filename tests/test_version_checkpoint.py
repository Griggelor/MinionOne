"""Versioned dev artifact must remain internally consistent."""
import importlib.metadata
from pathlib import Path
import tomllib
import unittest
from minionone import __version__

ROOT=Path(__file__).resolve().parents[1]
EXPECTED="0.21.0.dev0"

class TestDevelopmentVersion(unittest.TestCase):
    def test_packaged_and_runtime_version_match_milestone(self):
        meta=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(meta["project"]["version"],EXPECTED)
        self.assertEqual(__version__,EXPECTED)
        self.assertEqual(importlib.metadata.version("minionone"),EXPECTED)

    def test_ci_artifact_and_readme_identify_same_pre_alpha(self):
        workflow=(ROOT/".github/workflows/pilot-wheel.yml").read_text(encoding="utf-8")
        readme=(ROOT/"README.md").read_text(encoding="utf-8")
        self.assertIn("MinionOne-MCP-PILOT-UNVERIFIED-"+EXPECTED,workflow)
        self.assertIn(EXPECTED,readme)
        self.assertIn("NOT A BETA",readme)
        self.assertIn("NO-GO",readme)

if __name__=="__main__":
    unittest.main()
