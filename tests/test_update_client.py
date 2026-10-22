import copy
import hashlib
import io
import json
import stat
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

import update_client as updates


class FakeResponse(io.BytesIO):
    def __init__(self, content, url, headers=None):
        super().__init__(content)
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("Unexpected network request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class UpdateClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.version = "1.9.1"
        self.files = {
            "IoMarques Brecho.exe": b"MZ-test-application",
            "IoMarques Atualizador.exe": b"MZ-test-updater",
            "integracao_supabase.json": json.dumps({
                "supabase_url": "https://example.supabase.co",
                "supabase_publishable_key": "sb_publishable_" + "a" * 30,
                "auth_email_base": "test@example.com",
            }).encode(),
        }
        self.manifest = {
            "schema": 1, "version": self.version,
            "files": {name: {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
                      for name, value in self.files.items()},
        }
        self.events = []
        self.client = updates.UpdateClient("1.9.0", self.cache,
                                          lambda event, payload: self.events.append((event, payload)))
        self.addCleanup(self.client.shutdown)

    def make_package(self, files=None, manifest=None, extra=None, mode=None,
                     filename="release.zip", compression=zipfile.ZIP_DEFLATED):
        target = self.root / filename
        with zipfile.ZipFile(target, "w", compression=compression) as archive:
            for name, value in (files or self.files).items():
                if mode and name in mode:
                    entry = zipfile.ZipInfo(name)
                    entry.create_system = 3
                    entry.external_attr = mode[name] << 16
                    archive.writestr(entry, value)
                else:
                    archive.writestr(name, value)
            archive.writestr("release.json", json.dumps(manifest or self.manifest))
            for name, value in extra or []:
                archive.writestr(name, value)
        return target

    def signature(self, path):
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest(), len(content)

    def validate(self, path=None, **kwargs):
        path = path or self.make_package()
        sha256, size = self.signature(path)
        return updates.validate_package(path, kwargs.get("version", self.version),
                                        kwargs.get("sha256", sha256), kwargs.get("size", size))

    def release(self, **changes):
        path = self.make_package()
        sha256, size = self.signature(path)
        release = {
            "tag_name": "v" + self.version, "draft": False, "prerelease": False,
            "assets": [{"name": updates.ASSET_NAME, "size": size,
                        "digest": "sha256:" + sha256,
                        "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/"
                                                f"download/v{self.version}/{updates.ASSET_NAME}"}],
        }
        release.update(changes)
        return path, release

    def opener(self, release, package=None, headers=None, final_url=None):
        responses = [FakeResponse(json.dumps(release).encode(), updates.LATEST_RELEASE_URL)]
        if package is not None:
            responses.append(FakeResponse(package, final_url or release["assets"][0]["browser_download_url"],
                                          headers))
        self.client._opener = FakeOpener(responses)
        return self.client._opener

    def test_stable_versions_only(self):
        self.assertEqual(updates.parse_version("10.2.3"), (10, 2, 3))
        for value in [None, True, 123, "v1.2.3", "01.2.3", "1.2", "1.2.3-beta", " 1.2.3",
                      "1.2.3\n", "1.2.3.4", "-1.2.3", "1000000000.2.3"]:
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                updates.parse_version(value)

    def test_valid_package_manifest(self):
        self.assertEqual(self.validate(), self.manifest)

    def test_package_hash_size_and_version_are_mandatory(self):
        for changes in [{"sha256": "0" * 64}, {"sha256": None}, {"size": True},
                        {"size": 1}, {"size": updates.MAX_PACKAGE_SIZE + 1},
                        {"version": "2.0.0"}]:
            with self.subTest(changes=changes), self.assertRaises(updates.UpdateError):
                self.validate(**changes)

    def test_package_hash_accepts_uppercase_hex(self):
        package = self.make_package()
        sha256, size = self.signature(package)
        self.assertEqual(updates.validate_package(package, self.version, sha256.upper(), size),
                         self.manifest)

    def test_corrupted_archive_rejected_even_when_outer_hash_matches(self):
        package = self.root / "broken.zip"
        package.write_bytes(b"not a zip archive")
        with self.assertRaises(updates.UpdateError):
            self.validate(package)

    def test_extra_unknown_nested_and_traversal_entries_rejected(self):
        for name in ["user.dat", "../app.py", "C:\\app.py", "subdir/release.json", "empty/"]:
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                self.validate(self.make_package(extra=[(name, b"test")]))

    def test_duplicate_zip_entry_rejected(self):
        with self.assertWarns(UserWarning):
            package = self.make_package(extra=[("release.json", b"{}")])
        with self.assertRaises(updates.UpdateError):
            self.validate(package)

    def test_missing_file_rejected(self):
        files = dict(self.files)
        files.pop("IoMarques Brecho.exe")
        with self.assertRaises(updates.UpdateError):
            self.validate(self.make_package(files=files))

    def test_symlink_and_special_file_entries_rejected(self):
        for mode in [stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o755, stat.S_IFIFO | 0o600]:
            with self.subTest(mode=mode), self.assertRaises(updates.UpdateError):
                self.validate(self.make_package(mode={"IoMarques Brecho.exe": mode}))

    def test_payload_hash_and_size_are_checked(self):
        for field, value in [("sha256", "0" * 64), ("size", 1), ("size", True),
                             ("sha256", "not-a-digest")]:
            manifest = copy.deepcopy(self.manifest)
            manifest["files"]["IoMarques Brecho.exe"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(updates.UpdateError):
                self.validate(self.make_package(manifest=manifest))

    def test_manifest_requires_exact_schema_and_files(self):
        invalid = []
        for change in [{"schema": 2}, {"schema": True}, {"version": "1.8.0"}, {"unknown": True}]:
            manifest = copy.deepcopy(self.manifest)
            manifest.update(change)
            invalid.append(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["files"]["user.dat"] = {"size": 1, "sha256": "0" * 64}
        invalid.append(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["files"]["IoMarques Brecho.exe"]["extra"] = True
        invalid.append(manifest)
        for manifest in invalid:
            with self.subTest(manifest=manifest), self.assertRaises(updates.UpdateError):
                self.validate(self.make_package(manifest=manifest))

    def test_duplicate_manifest_json_fields_rejected(self):
        package = self.root / "duplicate.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for name, value in self.files.items():
                archive.writestr(name, value)
            archive.writestr("release.json", '{"schema":1,"schema":1,"version":"1.9.1","files":{}}')
        with self.assertRaises(updates.UpdateError):
            self.validate(package)

    def test_zipbomb_ratio_rejected(self):
        files = dict(self.files)
        files["IoMarques Brecho.exe"] = b"0" * 300000
        manifest = copy.deepcopy(self.manifest)
        manifest["files"]["IoMarques Brecho.exe"] = {
            "size": 300000, "sha256": hashlib.sha256(files["IoMarques Brecho.exe"]).hexdigest()}
        with self.assertRaises(updates.UpdateError):
            self.validate(self.make_package(files=files, manifest=manifest))

    def test_unpacked_size_limit_rejected(self):
        with patch.object(updates, "MAX_UNPACKED_SIZE", 50), self.assertRaises(updates.UpdateError):
            self.validate()

    def test_secret_or_extra_configuration_rejected(self):
        for changes in [{"supabase_publishable_key": "sb_secret_" + "a" * 30},
                        {"service_role": "never-package-a-secret"},
                        {"supabase_url": "http://example.supabase.co"}]:
            files = dict(self.files)
            config = json.loads(files["integracao_supabase.json"])
            config.update(changes)
            files["integracao_supabase.json"] = json.dumps(config).encode()
            manifest = copy.deepcopy(self.manifest)
            manifest["files"]["integracao_supabase.json"] = {
                "size": len(files["integracao_supabase.json"]),
                "sha256": hashlib.sha256(files["integracao_supabase.json"]).hexdigest(),
            }
            with self.subTest(changes=changes), self.assertRaises(updates.UpdateError):
                self.validate(self.make_package(files=files, manifest=manifest))

    def test_extract_into_new_empty_directory(self):
        package = self.make_package()
        sha256, size = self.signature(package)
        destination = self.root / "staging"
        result = updates.extract_verified_package(package, destination, self.version, sha256, size)
        self.assertEqual(result, self.manifest)
        self.assertEqual({path.name for path in destination.iterdir()}, updates.PACKAGE_FILES)
        for name, value in self.files.items():
            self.assertEqual((destination / name).read_bytes(), value)

    def test_extract_does_not_overwrite_user_file(self):
        package = self.make_package()
        sha256, size = self.signature(package)
        destination = self.root / "staging"
        destination.mkdir()
        existing = destination / "keep.dat"
        existing.write_bytes(b"keep-me")
        with self.assertRaises(updates.UpdateError):
            updates.extract_verified_package(package, destination, self.version, sha256, size)
        self.assertEqual(existing.read_bytes(), b"keep-me")
        self.assertEqual(len(list(destination.iterdir())), 1)

    def test_extraction_exclusive_creation_cleans_its_partial_files(self):
        package = self.make_package()
        sha256, size = self.signature(package)
        destination = self.root / "staging"
        original_open = Path.open

        def failed_open(path, *args, **kwargs):
            if path.name == "IoMarques Brecho.exe" and args == ("xb",):
                raise OSError("Simulated disk full")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", failed_open), self.assertRaises(updates.UpdateError):
            updates.extract_verified_package(package, destination, self.version, sha256, size)
        self.assertEqual(list(destination.iterdir()), [])

    def test_extraction_checks_hashes_again_including_manifest(self):
        package = self.make_package()
        sha256, size = self.signature(package)
        original_validate = updates._validate_open_package
        for changed_name in ["IoMarques Brecho.exe", "release.json"]:
            destination = self.root / ("staging-" + changed_name.replace(".", "-"))

            def change_after_validation(*args):
                archive, manifest = original_validate(*args)
                original_open = archive.open

                def changed_open(name, *open_args, **open_kwargs):
                    if name == changed_name:
                        value = b"?" * archive.getinfo(name).file_size
                        return io.BytesIO(value)
                    return original_open(name, *open_args, **open_kwargs)

                archive.open = changed_open
                return archive, manifest

            with self.subTest(name=changed_name), \
                    patch.object(updates, "_validate_open_package", side_effect=change_after_validation), \
                    self.assertRaises(updates.UpdateError):
                updates.extract_verified_package(package, destination, self.version, sha256, size)
            self.assertEqual(list(destination.iterdir()), [])

    def test_linked_paths_rejected(self):
        package = self.make_package()
        destination = self.root / "staging"
        with patch.object(updates, "_is_reparse", return_value=True):
            with self.assertRaises(updates.UpdateError):
                self.validate(package)
            sha256, size = self.signature(package)
            with self.assertRaises(updates.UpdateError):
                updates.extract_verified_package(package, destination, self.version, sha256, size)

    def test_only_allowlisted_https_redirects(self):
        for url in ["http://github.com/x", "https://evil.example/x", "https://github.com.evil/x",
                    "https://user@github.com/x", "https://github.com:444/x",
                    "https://github.com/x#fragment", "https://github.com\\evil/x"]:
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                updates._safe_url(url)
        for host in updates.ALLOWED_HOSTS:
            self.assertEqual(updates._safe_url(f"https://{host}/x?token=signed"),
                             f"https://{host}/x?token=signed")

    def test_redirect_handler_rejects_disallowed_destination(self):
        handler = updates._SafeRedirectHandler()
        request = urllib.request.Request(updates.LATEST_RELEASE_URL)
        with self.assertRaises(updates.UpdateError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/file")

    def test_automatic_download_and_ready_metadata(self):
        path, release = self.release()
        content = path.read_bytes()
        opener = self.opener(release, content, {"Content-Length": str(len(content))},
                             "https://release-assets.githubusercontent.com/signed?key=1")
        self.client._run()
        self.assertEqual(self.events[0], ("checking", {}))
        self.assertEqual(self.events[-1][0], "ready")
        ready = self.events[-1][1]
        self.assertEqual(ready["version"], self.version)
        self.assertEqual(ready["sha256"], release["assets"][0]["digest"][7:])
        self.assertEqual(ready["size"], len(content))
        self.assertEqual(Path(ready["path"]).read_bytes(), content)
        self.assertEqual([path.suffix for path in self.cache.iterdir()], [".zip"])
        self.assertEqual(len(opener.requests), 2)
        for request, timeout in opener.requests:
            self.assertEqual(timeout, 15)
            self.assertIsNone(request.get_header("Authorization"))
        self.assertIn(("downloading", {"percent": 100, "version": self.version}), self.events)

    def test_verified_cache_reused_without_downloading(self):
        path, release = self.release()
        self.opener(release, path.read_bytes())
        self.client._run()
        ready = self.events[-1][1]
        self.events.clear()
        opener = self.opener(release)
        self.client._run()
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(self.events[-1], ("ready", ready))

    def test_current_version_never_downloads(self):
        for tag in ["v1.9.0", "v1.8.9"]:
            path, release = self.release(tag_name=tag)
            opener = self.opener(release)
            self.events.clear()
            self.client._run()
            self.assertEqual(self.events[-1], ("current", {"version": "1.9.0"}))
            self.assertEqual(len(opener.requests), 1)
            self.assertFalse(self.cache.exists())

    def test_release_metadata_fail_closed(self):
        path, good = self.release()
        invalid = []
        for change in [{"draft": True}, {"prerelease": True}, {"tag_name": "1.9.1"},
                       {"tag_name": "v1.9.1-beta"}, {"assets": []}]:
            release = copy.deepcopy(good)
            release.update(change)
            invalid.append(release)
        for change in [{"digest": None}, {"digest": "sha256:bad"}, {"size": True},
                       {"size": updates.MAX_PACKAGE_SIZE + 1},
                       {"browser_download_url": "https://github.com/evil/repo/releases/file"}]:
            release = copy.deepcopy(good)
            release["assets"][0].update(change)
            invalid.append(release)
        release = copy.deepcopy(good)
        release["assets"].append(copy.deepcopy(release["assets"][0]))
        invalid.append(release)
        for release in invalid:
            with self.subTest(release=release):
                self.events.clear()
                opener = self.opener(release)
                self.client._run()
                self.assertEqual(self.events[-1][0], "error")
                self.assertEqual(len(opener.requests), 1)
                self.assertFalse(self.cache.exists())

    def test_download_size_content_length_and_hash_errors_remove_temporary_files(self):
        path, release = self.release()
        for content, headers in [(b"wrong", {}), (path.read_bytes() + b"extra", {}),
                                 (path.read_bytes(), {"Content-Length": "1"})]:
            self.events.clear()
            self.opener(release, content, headers)
            self.client._run()
            self.assertEqual(self.events[-1][0], "error")
            self.assertEqual(list(self.cache.iterdir()), [])

    def test_unsafe_download_final_url_rejected(self):
        path, release = self.release()
        self.opener(release, path.read_bytes(), final_url="http://github.com/insecure")
        self.client._run()
        self.assertEqual(self.events[-1][0], "error")
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_oversized_metadata_rejected(self):
        self.client._opener = FakeOpener([FakeResponse(b" " * (updates.MAX_METADATA_SIZE + 1),
                                                     updates.LATEST_RELEASE_URL)])
        self.client._run()
        self.assertEqual(self.events[-1][0], "error")
        self.assertFalse(self.cache.exists())

    def test_network_exception_does_not_expose_sensitive_details(self):
        self.client._opener = FakeOpener([OSError("secret-password-and-token")])
        self.client._run()
        self.assertEqual(self.events[-1][0], "error")
        self.assertNotIn("secret-password", self.events[-1][1]["message"])

    def test_error_allows_manual_retry(self):
        self.client._opener = FakeOpener([OSError("offline")])
        self.assertTrue(self.client.start())
        self.client._thread.join(timeout=3)
        self.assertEqual(self.events[-1][0], "error")
        path, release = self.release(tag_name="v1.9.0")
        self.opener(release)
        self.assertTrue(self.client.check_now())
        self.client._thread.join(timeout=3)
        self.assertEqual(self.events[-1][0], "current")

    def test_shutdown_cancels_callbacks_and_future_checks(self):
        self.client.shutdown()
        self.assertFalse(self.client.start())
        self.client._run()
        self.assertEqual(self.events, [])

    def test_does_not_start_duplicate_workers(self):
        started = threading.Event()
        release_worker = threading.Event()

        def blocked():
            started.set()
            release_worker.wait(3)

        with patch.object(self.client, "_run", side_effect=blocked):
            self.assertTrue(self.client.start())
            self.assertTrue(started.wait(2))
            self.assertFalse(self.client.check_now())
            release_worker.set()
            self.client._thread.join(timeout=3)

    def test_cancel_in_middle_of_download_removes_partial_file(self):
        path, release = self.release()
        original_request = self.client._request
        self.opener(release, path.read_bytes())

        def cancel_after_response(url, accept):
            response = original_request(url, accept)
            if accept == "application/octet-stream":
                self.client.shutdown()
            return response

        with patch.object(self.client, "_request", side_effect=cancel_after_response):
            self.client._run()
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertFalse(any(event in {"error", "ready"} for event, payload in self.events))

    def test_callback_exception_does_not_crash_worker(self):
        self.client.callback = lambda *args: (_ for _ in ()).throw(RuntimeError("closed UI"))
        path, release = self.release(tag_name="v1.9.0")
        self.opener(release)
        self.client._run()


if __name__ == "__main__":
    unittest.main()
