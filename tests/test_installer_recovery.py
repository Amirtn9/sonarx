"""No root, apt, network, or PostgreSQL server needed for installer regressions."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"install.sh"

class InstallerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.app=base/"app"
        self.app.mkdir()
        self.persist=base/"persistent"
        self.backup=base/"backups"
        (self.app/".venv/bin").mkdir(parents=True)
        (self.app/".venv/bin/python").symlink_to(sys.executable)
        self.env=os.environ.copy()
        self.env.update(SONARX_INSTALL_LIB_ONLY="1",SONARX_APP_DIR=str(self.app),
                        SONARX_PERSIST_DIR=str(self.persist),SONARX_BACKUP_DIR=str(self.backup))

    def bash(self, command):
        return subprocess.run(["bash","-c",f'source "$SONARX_SCRIPT"; {command}'],
                              env={**self.env,"SONARX_SCRIPT":str(SCRIPT)},
                              capture_output=True,text=True,timeout=10)

    def test_missing_dotfile_uses_fallback_outside_git_checkout(self):
        result=self.bash('template="$(ensure_env_template)"; test -f "$template"; grep -qx "BOT_TOKEN=replace_me" "$template"; test ! -e "$APP_DIR/.env.example"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.persist/"env.defaults").is_file())
        self.assertFalse((self.app/".env.example").exists())

    def test_existing_template_is_used_without_replacing_it(self):
        example=self.app/".env.example"
        example.write_text("BOT_TOKEN=some-template\n")
        result=self.bash('test "$(ensure_env_template)" = "$APP_DIR/.env.example"; grep -qx "BOT_TOKEN=some-template" "$APP_DIR/.env.example"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.persist/"env.defaults").exists())

    def test_visible_template_is_used_when_dotfile_missing(self):
        visible=self.app/"env.example"
        visible.write_text("BOT_TOKEN=visible-default\n")
        result=self.bash('test "$(ensure_env_template)" = "$APP_DIR/env.example"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.persist/"env.defaults").exists())

    def test_backup_preserves_fernet_and_connection_string(self):
        original="BOT_TOKEN=private-token\nFERNET_KEY=old-fernet-key\nDATABASE_URL=postgresql+psycopg://sonarx:old@127.0.0.1:5432/sonarx\n"
        (self.app/".env").write_text(original)
        result=self.bash('backup_config')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.persist/".env").read_text(),original)
        self.assertEqual((self.persist/".env").stat().st_mode & 0o777,0o600)
        self.assertEqual(len(list((self.backup/"config").glob("*.env"))),1)

    def test_lost_configuration_requires_explicit_consent(self):
        result=self.bash('''runuser(){ echo 1; }; prompt(){ printf -v "$1" CANCEL; }; if confirm_lost_configuration; then exit 50; else exit 0; fi''')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("original .env is missing",result.stdout)

    def test_secret_configuration_update_via_stdin(self):
        (self.app/".env").write_text("BOT_TOKEN=replace_me\n")
        result=self.bash('set_env BOT_TOKEN "123456:private_new_token"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("BOT_TOKEN=123456:private_new_token",(self.app/".env").read_text())
        self.assertNotIn("private_new_token",result.stdout+result.stderr)

if __name__=="__main__":unittest.main()
