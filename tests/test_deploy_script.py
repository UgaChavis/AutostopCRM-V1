import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    def test_deploy_script_syncs_active_v1_branch_by_default(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn('DEPLOY_BRANCH="${AUTOSTOP_DEPLOY_BRANCH:-autostopcrm-v1}"', script)
        self.assertIn('DEPLOY_REMOTE="${AUTOSTOP_DEPLOY_REMOTE:-origin}"', script)
        self.assertIn('git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"', script)
        self.assertIn("git reset --hard FETCH_HEAD", script)

    def test_compose_declares_telegram_ai_worker(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("autostopcrm-telegram-ai:", compose)
        self.assertIn(
            'command: ["sh", "-lc", "set -a; . /run/telegram-ai.env; exec python main_telegram_ai.py"]',
            compose,
        )
        self.assertIn("telegram-ai.env:/run/telegram-ai.env:ro", compose)
        self.assertIn("telegram-ai.env", compose)


if __name__ == "__main__":
    unittest.main()
