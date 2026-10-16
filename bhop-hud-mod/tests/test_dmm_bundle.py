"""Тесты пакета для Deadlock Mod Manager (формат «Deadlock Mod Interchange»)."""

from __future__ import annotations

import hashlib
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path

import dmm_bundle
import vpk


def _make_vpk(path: Path) -> Path:
    path.write_bytes(vpk.build_vpk({"panorama/styles/bhop_trainer.vcss_c": b"css"}))
    return path


class DocumentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vpk_path = _make_vpk(self.tmp / "bhop_trainer_dir.vpk")
        self.file = dmm_bundle.BundleFile(name="bhop_trainer_dir.vpk", source=self.vpk_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _document(self, **kwargs) -> dict:
        options = dict(
            files=[self.file], entry_folder="0000_bhop_trainer", title="Deadlock :: Bhop Trainer",
            author="owlclockl", description="desc", category="UI/HUD", enabled=True, order=0,
        )
        options.update(kwargs)
        return dmm_bundle.build_document(**options)

    def test_document_shape_matches_rfc(self) -> None:
        document = self._document()
        self.assertEqual("deadlock-mod-interchange", document["format"])
        self.assertEqual(1, document["version"])
        self.assertEqual(["mods"], document["contents"])
        self.assertEqual({"manager": "sic-bhop-toolchain", "managerVersion": "1.0.0"}, document["source"])

        mod = document["mods"][0]
        sha = hashlib.sha256(self.vpk_path.read_bytes()).hexdigest()
        self.assertEqual(f"local:{sha}", mod["key"])
        self.assertEqual("local", mod["origin"]["provider"])
        self.assertTrue(mod["enabled"])
        self.assertEqual(0, mod["order"])
        self.assertEqual("Deadlock :: Bhop Trainer", mod["name"])

        entry = mod["files"][0]
        self.assertEqual("bhop_trainer_dir.vpk", entry["name"])
        self.assertEqual("files/0000_bhop_trainer/bhop_trainer_dir.vpk", entry["path"])
        self.assertEqual(sha, entry["sha256"])
        self.assertEqual(self.vpk_path.stat().st_size, entry["size"])
        self.assertTrue(entry["selected"])

    def test_local_id_matches_dmm_uuid_derivation(self) -> None:
        document = self._document()
        key = document["mods"][0]["key"]
        local_id = document["mods"][0]["origin"]["localId"]
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        expected = (
            f"local-{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
        )
        self.assertEqual(expected, local_id)
        self.assertRegex(local_id, r"^local-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertEqual(local_id, dmm_bundle.local_id(key))  # детерминизм

    def test_key_is_stable_for_same_file(self) -> None:
        self.assertEqual(self._document()["mods"][0]["key"], self._document()["mods"][0]["key"])

    def test_empty_file_list_raises(self) -> None:
        with self.assertRaises(dmm_bundle.BundleError):
            self._document(files=[])

    def test_slug_matches_manager_style(self) -> None:
        self.assertEqual("qol_lock", dmm_bundle.slug("QOL Lock"))
        self.assertEqual("0000_bhop_trainer", dmm_bundle.slug("0000-bhop_trainer"))


class BundleTest(DocumentTest):
    def test_write_and_verify_bundle(self) -> None:
        bundle = self.tmp / "dmm-bundle"
        document = self._document()
        dmm_bundle.write_bundle(bundle, document, [self.file])
        self.assertTrue((bundle / "mod-interchange.json").is_file())
        self.assertTrue((bundle / "files" / "0000_bhop_trainer" / "bhop_trainer_dir.vpk").is_file())
        self.assertEqual([], dmm_bundle.verify_bundle(bundle))

        summary = dmm_bundle.bundle_summary(bundle)
        self.assertEqual(document["mods"][0]["key"], summary["key"])
        self.assertEqual(document["mods"][0]["origin"]["localId"], summary["localId"])

    def test_verify_detects_tampered_payload(self) -> None:
        bundle = self.tmp / "dmm-bundle"
        dmm_bundle.write_bundle(bundle, self._document(), [self.file])
        target = bundle / "files" / "0000_bhop_trainer" / "bhop_trainer_dir.vpk"
        target.write_bytes(b"other bytes")
        problems = dmm_bundle.verify_bundle(bundle)
        self.assertTrue(any("size" in problem for problem in problems), problems)
        self.assertTrue(any("sha256" in problem for problem in problems), problems)

    def test_verify_detects_bad_manifest_fields(self) -> None:
        bundle = self.tmp / "dmm-bundle"
        document = self._document()
        dmm_bundle.write_bundle(bundle, document, [self.file])
        manifest = bundle / "mod-interchange.json"

        document["mods"][0]["files"][0]["path"] = "../outside.vpk"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(any("выходит за пределы" in problem for problem in dmm_bundle.verify_bundle(bundle)))

        document["mods"][0]["files"][0]["path"] = "files/0000_bhop_trainer/bhop_trainer_dir.vpk"
        document["mods"][0]["origin"]["localId"] = "local-not-a-uuid"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(any("localId" in problem for problem in dmm_bundle.verify_bundle(bundle)))

        document["mods"][0]["origin"]["localId"] = dmm_bundle.local_id(document["mods"][0]["key"])
        document["format"] = "something-else"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(any("format" in problem for problem in dmm_bundle.verify_bundle(bundle)))

    def test_verify_reports_missing_file(self) -> None:
        bundle = self.tmp / "dmm-bundle"
        dmm_bundle.write_bundle(bundle, self._document(), [self.file])
        (bundle / "files" / "0000_bhop_trainer" / "bhop_trainer_dir.vpk").unlink()
        self.assertTrue(any("нет в пакете" in problem for problem in dmm_bundle.verify_bundle(bundle)))

    def test_zip_contains_manifest_vpk_and_notes(self) -> None:
        bundle = self.tmp / "dmm-bundle"
        dmm_bundle.write_bundle(bundle, self._document(), [self.file])
        archive = dmm_bundle.write_zip(self.tmp / "mod.zip", bundle, notes="инструкция")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        self.assertIn("mod-interchange.json", names)
        self.assertIn("files/0000_bhop_trainer/bhop_trainer_dir.vpk", names)
        self.assertIn("README-INSTALL.txt", names)

    def test_bundle_zip_can_be_reimported_as_bundle(self) -> None:
        """Один zip работает и как пакет, и как архив для перетаскивания."""
        bundle = self.tmp / "dmm-bundle"
        dmm_bundle.write_bundle(bundle, self._document(), [self.file])
        archive = dmm_bundle.write_zip(self.tmp / "mod.zip", bundle)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(self.tmp / "unpacked")
        unpacked = self.tmp / "unpacked"
        self.assertEqual([], dmm_bundle.verify_bundle(unpacked))
        found = list(unpacked.rglob("*.vpk"))
        self.assertEqual(1, len(found))


if __name__ == "__main__":
    unittest.main()
