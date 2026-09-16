import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import preparar_config_integracao as installer_config
from supabase_sync import _validated_config, load_sync_config


PUBLIC_KEY = "sb_publishable_" + "test_public_key_for_install_1234"
FIELDS = {"supabase_url", "supabase_publishable_key", "auth_email_base"}


def public_values(**overrides):
    values = {
        "supabase_url": "https://example.supabase.co",
        "supabase_publishable_key": PUBLIC_KEY,
        "auth_email_base": "acesso@example.invalid",
    }
    return {**values, **overrides}


def jwt(role):
    def encoded(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f"{encoded({'alg': 'HS256'})}.{encoded({'role': role})}.signature"


class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "download" / "iomarques-live-main"
        self.root.mkdir(parents=True)
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def save(self, filename, values, directory=None):
        path = (directory or self.root) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def test_isolated_github_zip_has_all_public_configuration(self):
        self.save("config_publica.json", public_values())
        output = self.root.parent / "installed" / "integracao_supabase.json"
        self.assertEqual(installer_config.write_config(output, root=self.root), output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), public_values())
        self.assertFalse((self.root.parent / "brecho-controle").exists())

    def test_bundled_resources_work_when_app_data_directory_is_separate(self):
        self.save("config_publica.json", public_values())
        app_dir = self.root.parent / "data"
        config = load_sync_config(app_dir, self.root)
        self.assertEqual(config.publishable_key, PUBLIC_KEY)

    def test_source_local_configuration_overrides_public_default(self):
        self.save("config_publica.json", public_values())
        self.save("integracao_supabase.local.json", public_values(supabase_url="https://override.supabase.co"))
        config = load_sync_config(self.root.parent / "data", self.root)
        self.assertEqual(config.url, "https://override.supabase.co")

    def test_installed_configuration_precedes_source_local_override(self):
        self.save("integracao_supabase.local.json", public_values(supabase_url="https://source.supabase.co"))
        app_dir = self.root.parent / "installed"
        self.save("integracao_supabase.json", public_values(), app_dir)
        self.assertEqual(load_sync_config(app_dir, self.root).url, public_values()["supabase_url"])

    def test_environment_overrides_files(self):
        self.save("config_publica.json", public_values())
        os.environ.update({
            "IOMARQUES_SUPABASE_URL": "https://environment.supabase.co",
            "IOMARQUES_SUPABASE_PUBLISHABLE_KEY": PUBLIC_KEY,
            "IOMARQUES_AUTH_EMAIL_BASE": "other@example.invalid",
        })
        self.assertEqual(load_sync_config(self.root).url, "https://environment.supabase.co")

    def test_sibling_dotenv_precedes_public_default(self):
        self.save("config_publica.json", public_values())
        sibling = self.root.parent / "brecho-controle"
        sibling.mkdir()
        (sibling / ".env.local").write_text(
            'VITE_SUPABASE_URL="https://sibling.supabase.co"\n'
            f'VITE_SUPABASE_PUBLISHABLE_KEY={PUBLIC_KEY}\n'
            'VITE_AUTH_EMAIL_BASE=acesso@example.invalid\n', encoding="utf-8"
        )
        self.assertEqual(load_sync_config(self.root, self.root).url, "https://sibling.supabase.co")

    def test_privileged_key_in_environment_does_not_fall_back(self):
        self.save("config_publica.json", public_values())
        os.environ.update({
            "IOMARQUES_SUPABASE_URL": "https://example.supabase.co",
            "IOMARQUES_SUPABASE_PUBLISHABLE_KEY": jwt("service_role"),
            "IOMARQUES_AUTH_EMAIL_BASE": "acesso@example.invalid",
        })
        self.assertIsNone(load_sync_config(self.root))
        with self.assertRaises(ValueError):
            installer_config.write_config(self.root / "out.json", root=self.root)

    def test_incomplete_environment_does_not_select_another_project(self):
        self.save("config_publica.json", public_values())
        os.environ["IOMARQUES_SUPABASE_URL"] = "https://partial.supabase.co"
        self.assertIsNone(load_sync_config(self.root))

    def test_privileged_local_override_does_not_fall_back(self):
        self.save("config_publica.json", public_values())
        self.save("integracao_supabase.local.json", public_values(supabase_publishable_key="sb_secret_private"))
        self.assertIsNone(load_sync_config(self.root))

    def test_legacy_anon_key_is_public(self):
        self.assertIsNotNone(_validated_config(public_values(supabase_publishable_key=jwt("anon"))))

    def test_secret_service_role_and_user_keys_are_rejected(self):
        for key in ("sb_secret_example", jwt("service_role"), jwt("authenticated"), "service_role", "abc", "a.invalid.c"):
            with self.subTest(key_type=key[:12]):
                self.assertIsNone(_validated_config(public_values(supabase_publishable_key=key)))

    def test_unsafe_urls_are_rejected(self):
        for url in ("http://example.com", "https://", "https://user:pass@example.com", "https://@example.com", "https://example.com/path", "https://example.com?query=x", "https://example.com#fragment", "https://exa mple.com", "https://example.com:444", "https://[broken"):
            with self.subTest(url=url):
                self.assertIsNone(_validated_config(public_values(supabase_url=url)))

    def test_invalid_email_and_field_types_are_rejected(self):
        for value in ("invalid", "name@", "@example.com", "name@example", "name@exa mple.com", None, 123):
            with self.subTest(value=value):
                self.assertIsNone(_validated_config(public_values(auth_email_base=value)))

    def test_missing_and_corrupted_files_do_not_produce_config(self):
        output = self.root / "out.json"
        with self.assertRaises(ValueError):
            installer_config.write_config(output, root=self.root)
        self.assertFalse(output.exists())
        (self.root / "config_publica.json").write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(load_sync_config(self.root))

    def test_only_allowlisted_fields_exported_no_session_or_password(self):
        self.save("integracao_supabase.local.json", public_values(password="private", access_token="private", refresh_token="private", extra="unused"))
        (self.root / "integracao_sessao.dat").write_bytes(b"session-must-not-be-copied")
        output = self.root.parent / "installed" / "integracao_supabase.json"
        installer_config.write_config(output, root=self.root)
        self.assertEqual(set(json.loads(output.read_text(encoding="utf-8"))), FIELDS)
        self.assertEqual([path.name for path in output.parent.iterdir()], [output.name])

    def test_invalid_source_preserves_existing_destination(self):
        output = self.root / "existing.json"
        output.write_text("previous-valid-installation", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer_config.write_config(output, root=self.root)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous-valid-installation")

    def test_json_bom_and_trailing_url_slash_supported(self):
        path = self.root / "config_publica.json"
        path.write_text(json.dumps(public_values(supabase_url="https://example.supabase.co/")), encoding="utf-8-sig")
        self.assertEqual(load_sync_config(self.root).url, "https://example.supabase.co")

    def test_repository_public_config_is_valid_and_allowlisted(self):
        path = Path(__file__).resolve().parents[1] / "config_publica.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(value), FIELDS)
        self.assertIsNotNone(_validated_config(value))

    def test_cli_accepts_output_and_fails_when_configuration_invalid(self):
        output = self.root / "cli-output.json"
        with patch.object(installer_config, "write_config", return_value=output) as writer, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer_config.main(["--output", str(output)]), 0)
            writer.assert_called_once_with(output)
        with patch.object(installer_config, "write_config", side_effect=ValueError("invalid")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer_config.main(["--output", str(output)]), 1)


if __name__ == "__main__":
    unittest.main()
