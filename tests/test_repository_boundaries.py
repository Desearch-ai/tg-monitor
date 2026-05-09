import os
import stat
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RADAR_FILES = {
    "tg_hot_topics_context.py",
    "tg_radar_context_compact.sh",
    "tg_radar_report.sh",
    "hot_topics_cron_prompt.md",
}
EXECUTABLE_RADAR_FILES = REQUIRED_RADAR_FILES - {"hot_topics_cron_prompt.md"}


class RepositoryBoundaryTests(unittest.TestCase):
    def git_ls_files(self, *patterns: str) -> set[str]:
        result = subprocess.run(
            ["git", "ls-files", *patterns],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return {line for line in result.stdout.splitlines() if line}

    def test_runtime_artifacts_are_not_source_controlled(self):
        tracked = self.git_ls_files(
            ".env",
            "monitor.db",
            "monitor.db*",
            "monitor.log",
            "snapshot_nerds.json",
            "snapshot_state.json",
            "health.json",
            "user_session.session*",
            "*.session",
            "*.session-journal",
        )

        self.assertFalse(
            tracked,
            f"Runtime/private artifacts must not be tracked: {sorted(tracked)}",
        )

    def test_radar_workflow_files_are_source_controlled_and_executable(self):
        tracked = self.git_ls_files(*REQUIRED_RADAR_FILES)

        self.assertEqual(REQUIRED_RADAR_FILES, tracked)
        for rel_path in EXECUTABLE_RADAR_FILES:
            mode = os.stat(REPO_ROOT / rel_path).st_mode
            self.assertTrue(
                mode & stat.S_IXUSR,
                f"{rel_path} should stay executable for cron/manual runs",
            )


if __name__ == "__main__":
    unittest.main()
