import os
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import torch
from PIL import Image

from pe_runtime import (DEFAULT_EDIT, DEFAULT_T2I, DEFAULT_MMPROJ,
                        LocalServer, file_signature, local_models,
                        load_system_prompt, normalize_single_image_references, parse_answer, parse_edit_answer, pick_mmproj,
                        prepare_images, quoted_literals,
                        remove_opaque_background_sentences, validate_language, validate_mode,
                        validate_references)


class RuntimeContractTests(unittest.TestCase):
    def test_parse_t2i_json_after_thinking_text(self):
        raw = '<think>the example {"wrong": 1} is invalid</think>\n{"rewritten_prompt":"a blue dog", "wh_ratio":"3:2"}'
        self.assertEqual(parse_answer(raw, "t2i", 0)["wh_ratio"], "3:2")

    def test_parse_rejects_thinking_only_and_nested_wrapper(self):
        draft = '<think>{"rewritten_prompt":"draft blue dog","wh_ratio":"1:1"}</think>'
        with self.assertRaisesRegex(ValueError, "valid final JSON"):
            parse_answer(draft, "t2i", 0)
        wrapped = '{"answer":{"rewritten_prompt":"blue dog","wh_ratio":"1:1"}}'
        with self.assertRaisesRegex(ValueError, "wrong answer fields"):
            parse_answer(wrapped, "t2i", 0)
        malformed_wrapper = '{"answer":{"rewritten_prompt":"blue dog","wh_ratio":"1:1"}'
        with self.assertRaises(ValueError):
            parse_answer(malformed_wrapper, "t2i", 0)
        with self.assertRaisesRegex(ValueError, "incomplete thinking"):
            parse_answer('<think>{"rewritten_prompt":"draft","wh_ratio":"1:1"}', "t2i", 0)
        literal = '{"rewritten_prompt":"A poster says \\"<think>HELLO</think>\\".","wh_ratio":"1:1"}'
        self.assertIn("<think>HELLO</think>", parse_answer(literal, "t2i", 0)["rewritten_prompt"])

    def test_parse_rejects_extra_field_and_bad_image_reference(self):
        with self.assertRaises(ValueError):
            parse_answer('{"rewritten_prompt":"x","wh_ratio":"1:1","extra":"y"}', "t2i", 0)
        with self.assertRaises(ValueError):
            parse_answer('{"rewritten_prompt":"x","wh_ratio":"","ratio_follow":"<image11>"}', "edit", 10)
        with self.assertRaises(ValueError):
            parse_answer('{"rewritten_prompt":"x","wh_ratio":"1:1","ratio_follow":"<image1>"}', "edit", 1)

    def test_reference_contract(self):
        validate_references({"rewritten_prompt": "Use <image1> and <image2>."}, "edit", 2)
        nine_tags = " ".join(f"<image{i}>" for i in range(1, 10))
        validate_references({"rewritten_prompt": nine_tags}, "edit", 9)
        with self.assertRaises(ValueError):
            validate_references({"rewritten_prompt": nine_tags.replace("<image9>", "")}, "edit", 9)
        validate_references({"rewritten_prompt": "Preserve the original image."}, "edit", 1)
        validate_references({"rewritten_prompt": "Preserve <image1>."}, "edit", 1)
        validate_references({"rewritten_prompt": 'Print "<image1>" on the poster.'}, "t2i", 0)
        validate_references({"rewritten_prompt": "Print '<image1>' on the poster."}, "t2i", 0)
        validate_references({"rewritten_prompt": 'Blend "<image1>" with "<image2>".'},
                            "edit", 2, set())
        validate_references({"rewritten_prompt": 'Print "<image1>" on the poster.'},
                            "t2i", 0, {"<image1>"})
        with self.assertRaises(ValueError):
            validate_references({"rewritten_prompt": "Use <image>."}, "edit", 1)
        with self.assertRaises(ValueError):
            validate_references({"rewritten_prompt": "Use <image1>."}, "edit", 2)
        with self.assertRaises(ValueError):
            validate_references({"rewritten_prompt": "Use <image01> and <image2>."}, "edit", 2)
        for prompt, task, count in (("Repaint <IMAGE2> blue.", "edit", 1),
                                    ("A poster of <IMAGE1>.", "t2i", 0),
                                    ("Use < Image1> and <image2>.", "edit", 2)):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                validate_references({"rewritten_prompt": prompt}, task, count)

    def test_ratio_and_transparency_conflicts_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_mode({"rewritten_prompt": "A butterfly in a square-format image with a dark background.",
                           "wh_ratio": "1:1", "ratio_follow": ""}, "21:9", True)
        with self.assertRaises(ValueError):
            validate_mode({"rewritten_prompt": "The background is a warm beige surface.",
                           "wh_ratio": "21:9", "ratio_follow": ""}, "21:9", True)
        validate_mode({"rewritten_prompt": "A butterfly isolated on a transparent background.",
                       "wh_ratio": "21:9", "ratio_follow": ""}, "21:9", True)

    def test_transparency_cleanup_removes_opaque_backdrop_only(self):
        prose, count = remove_opaque_background_sentences(
            "A golden butterfly has detailed wings. The background is a warm beige surface. "
            "Keep the butterfly centered. The image has a transparent background.")
        self.assertEqual(count, 1)
        self.assertIn("detailed wings", prose)
        self.assertNotIn("beige", prose)
        self.assertIn("transparent background", prose)

    def test_transparency_cleanup_preserves_numbers_and_exact_text(self):
        prompt = 'A 1.5 kg product reads "SALE!TODAY" on a transparent background.'
        self.assertEqual(remove_opaque_background_sentences(prompt, {"SALE!TODAY"}),
                         (prompt, 0))
        with_backdrop = ('A 1.5 kg product reads "SALE!TODAY". '
                         'The background is a warm beige surface. Keep its label unchanged.')
        cleaned, removed = remove_opaque_background_sentences(
            with_backdrop, {"SALE!TODAY"})
        self.assertEqual(removed, 1)
        self.assertEqual(cleaned, 'A 1.5 kg product reads "SALE!TODAY". '
                         'Keep its label unchanged.')

    def test_explicit_chinese_rejects_stray_english_but_preserves_literal_text(self):
        validate_language({"rewritten_prompt": "一张海报，标题写成\"FRESH BREAD\"。"}, "中文")
        validate_language({"rewritten_prompt": "一张海报，标题写成 'FRESH BREAD'。"}, "中文")
        for delimiters in ("「」", "『』", "«»"):
            prompt = f"一张海报，标题写成{delimiters[0]}FRESH BREAD{delimiters[1]}。"
            self.assertEqual(quoted_literals(prompt), ["FRESH BREAD"])
            validate_language({"rewritten_prompt": prompt}, "中文", set(quoted_literals(prompt)))
        validate_language({"rewritten_prompt": "A poster displays '你好' in red type."}, "English")
        validate_language({"rewritten_prompt": "Don't change the title '你好'."}, "English")
        with self.assertRaises(ValueError):
            validate_language({"rewritten_prompt": "一张 watercolor 海报。"}, "中文")
        with self.assertRaisesRegex(ValueError, "non-target writing scripts"):
            validate_language({"rewritten_prompt": "赤い猫の写真。"}, "中文", set())
        with self.assertRaisesRegex(ValueError, "non-target writing scripts"):
            validate_language({"rewritten_prompt": "빨간 고양이 사진."}, "English", set())

    def test_image_preparation_preserves_original_size_and_rejects_batch(self):
        image = torch.zeros((1, 1200, 1600, 3), dtype=torch.float32)
        encoded, dimensions = prepare_images([image])
        self.assertEqual(dimensions, [[1600, 1200]])
        self.assertTrue(encoded[0].startswith("data:image/png;base64,"))
        with Image.open(io.BytesIO(base64.b64decode(encoded[0].split(",", 1)[1]))) as reduced:
            self.assertLessEqual(reduced.width * reduced.height, 1024 * 1024)
        bf16 = torch.zeros((1, 1200, 1200, 3), dtype=torch.bfloat16)
        bf16_encoded, bf16_dimensions = prepare_images([bf16])
        self.assertEqual(bf16_dimensions, [[1200, 1200]])
        self.assertTrue(bf16_encoded[0].startswith("data:image/png;base64,"))
        needle, original = prepare_images([torch.zeros((1, 1, 8192, 3))])
        self.assertEqual(original, [[8192, 1]])
        with Image.open(io.BytesIO(base64.b64decode(needle[0].split(",", 1)[1]))) as reduced:
            self.assertEqual(reduced.size, (4096, 1))
        very_long, _ = prepare_images([torch.zeros((1, 1, 1_200_000, 3))])
        with Image.open(io.BytesIO(base64.b64decode(very_long[0].split(",", 1)[1]))) as reduced:
            self.assertLessEqual(reduced.width * reduced.height, 1024 * 1024)
            self.assertLessEqual(max(reduced.size), 4096)
        with self.assertRaises(ValueError):
            prepare_images([torch.zeros((2, 100, 100, 3))])

    def test_rgba_downscale_does_not_bleed_invisible_rgb_into_visible_pixels(self):
        image = torch.zeros((1, 1, 8192, 4))
        image[0, 0, ::2, 3] = 1.0  # opaque black
        image[0, 0, 1::2, 0] = 1.0  # transparent red, invisible before filtering
        encoded, _ = prepare_images([image])
        with Image.open(io.BytesIO(base64.b64decode(encoded[0].split(",", 1)[1]))) as reduced:
            red, green, blue = reduced.convert("RGB").getpixel((2048, 0))
        self.assertLessEqual(max(red, green, blue) - min(red, green, blue), 1)
        self.assertTrue(110 <= red <= 145)
        vertical, _ = prepare_images([image.permute(0, 2, 1, 3)])
        with Image.open(io.BytesIO(base64.b64decode(vertical[0].split(",", 1)[1]))) as reduced:
            red, green, blue = reduced.convert("RGB").getpixel((0, 2048))
        self.assertLessEqual(max(red, green, blue) - min(red, green, blue), 1)
        tiny = torch.zeros((1, 1, 1, 4))
        tiny[0, 0, 0, 0] = 1.0
        untouched, _ = prepare_images([tiny])
        with Image.open(io.BytesIO(base64.b64decode(untouched[0].split(",", 1)[1]))) as reduced:
            self.assertEqual(reduced.convert("RGB").getpixel((0, 0)), (255, 255, 255))
        tiny[0, 0, 0, 3] = float("nan")
        cleaned, _ = prepare_images([tiny])
        with Image.open(io.BytesIO(base64.b64decode(cleaned[0].split(",", 1)[1]))) as reduced:
            self.assertEqual(reduced.convert("RGB").getpixel((0, 0)), (255, 255, 255))
        tiny[0, 0, 0, :3] = 1.0
        tiny[0, 0, 0, 3] = float("inf")
        cleaned, _ = prepare_images([tiny])
        with Image.open(io.BytesIO(base64.b64decode(cleaned[0].split(",", 1)[1]))) as reduced:
            self.assertEqual(reduced.convert("RGB").getpixel((0, 0)), (255, 255, 255))

    def test_rgb_downscale_sanitizes_non_finite_pixels_before_filtering(self):
        image = torch.ones((1, 1, 8192, 3))
        image[0, 0, 4096, 0] = float("nan")
        encoded, _ = prepare_images([image])
        self.assertTrue(torch.isnan(image[0, 0, 4096, 0]))
        with Image.open(io.BytesIO(base64.b64decode(encoded[0].split(",", 1)[1]))) as reduced:
            center = reduced.convert("RGB").getpixel((2048, 0))
        self.assertGreater(center[0], 0)
        self.assertEqual(center[1:], (255, 255))

    def test_model_discovery_includes_additional_local_gguf(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "another-Q4.gguf").write_bytes(b"stub")
            (folder / "another.mmproj.gguf").write_bytes(b"stub")
            with patch.dict(os.environ, {"QWEN_PE_MODEL_DIR": str(folder)}):
                self.assertIn("another-Q4.gguf", local_models())
                self.assertIn("another.mmproj.gguf", local_models(True))
                self.assertNotIn("another.mmproj.gguf", local_models())

    def test_known_vision_project_cannot_pair_with_t2i_weight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (DEFAULT_EDIT, DEFAULT_T2I, DEFAULT_MMPROJ):
                (root / name).write_bytes(b"fixture")
            with patch("pe_runtime.model_roots", side_effect=lambda: iter([root])):
                self.assertEqual(pick_mmproj(DEFAULT_EDIT, "Auto").name, DEFAULT_MMPROJ)
            with patch("pe_runtime.model_roots", side_effect=lambda: iter([root])):
                with self.assertRaises(ValueError):
                    pick_mmproj(DEFAULT_T2I, DEFAULT_MMPROJ)

    def test_auto_vision_model_uses_the_model_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            name = "Example.Q4_K_M.gguf"
            vision = "Example.mmproj-f16.gguf"
            for folder in ("A", "B"):
                (root / folder).mkdir()
                (root / folder / vision).write_bytes(b"vision")
            (root / "A" / name).write_bytes(b"model")
            with patch.dict(os.environ, {"QWEN_PE_MODEL_DIR": str(root)}):
                self.assertEqual(pick_mmproj("A/" + name, "Auto"), root / "A" / vision)

    def test_auto_vision_model_ignores_duplicate_name_in_other_root(self):
        with tempfile.TemporaryDirectory() as temp:
            model_root, override_root = Path(temp) / "model", Path(temp) / "override"
            model_root.mkdir()
            override_root.mkdir()
            model = model_root / "Example.Q4_K_M.gguf"
            vision = "Example.mmproj-f16.gguf"
            model.write_bytes(b"main")
            (model_root / vision).write_bytes(b"correct")
            (override_root / vision).write_bytes(b"other")
            with patch("pe_runtime.model_roots", return_value=iter([override_root, model_root])):
                self.assertEqual(pick_mmproj(model.name, "Auto"), model_root / vision)

    def test_bf16_edit_weight_pairs_with_vision_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "Qwen-Image-2.1-PE-I2I.BF16.gguf"
            vision = root / "Qwen-Image-2.1-PE-I2I.mmproj-bf16.gguf"
            model.write_bytes(b"model")
            vision.write_bytes(b"vision")
            with patch("pe_runtime.model_roots", side_effect=lambda: iter([root])):
                self.assertEqual(pick_mmproj(model.name, "Auto"), vision)
                self.assertEqual(pick_mmproj(model.name, vision.name), vision)

    def test_hyphen_quantized_heretic_edit_pairs_with_own_vision_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "pe_i2i_heretic-Q4_K_M.gguf"
            vision = root / "pe_i2i_heretic.mmproj-bf16.gguf"
            other = root / "Qwen-Image-2.1-PE-I2I.mmproj-bf16.gguf"
            for path in (model, vision, other):
                path.write_bytes(b"stub")
            with patch("pe_runtime.model_roots", side_effect=lambda: iter([root])):
                self.assertEqual(pick_mmproj(model.name, "Auto"), vision)
                self.assertEqual(pick_mmproj(model.name, vision.name), vision)
                with self.assertRaises(ValueError):
                    pick_mmproj(model.name, other.name)

    def test_bonsai_packings_pair_with_matching_hyphenated_projector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vision = root / "Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf"
            other = root / "Another-27B-mmproj-Q8_0.gguf"
            vision.write_bytes(b"vision")
            other.write_bytes(b"other")
            for packing in ("PTQ1_0", "PQ2_0"):
                model = root / f"Ternary-Bonsai-2-27B-{packing}.gguf"
                model.write_bytes(b"model")
                with (self.subTest(packing=packing),
                      patch("pe_runtime.model_roots", side_effect=lambda: iter([root]))):
                    self.assertEqual(pick_mmproj(model.name, "Auto"), vision)
                    self.assertEqual(pick_mmproj(model.name, vision.name), vision)
                    with self.assertRaises(ValueError):
                        pick_mmproj(model.name, other.name)
                    second = root / "Ternary-Bonsai-2-27B-mmproj-BF16.gguf"
                    second.write_bytes(b"vision")
                    with self.assertRaisesRegex(ValueError, "unique matching"):
                        pick_mmproj(model.name, "Auto")
                    self.assertEqual(pick_mmproj(model.name, vision.name), vision)
                    second.unlink()

    def test_bonsai_heretic_accepts_verified_shared_q8_projector(self):
        import pe_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "Ternary-Bonsai-2-27B-Uncensored-Heretic-PQ2_0.gguf"
            model.write_bytes(b"model")
            fixture = b"trusted Q8 projector"
            shared_names = ("Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf", "mmproj-Q8_0.gguf")
            own = root / "Ternary-Bonsai-2-27B-Uncensored-Heretic-mmproj-Q8_0.gguf"
            own.write_bytes(fixture)
            for name in shared_names:
                (root / name).write_bytes(fixture)
            with (patch("pe_runtime.model_roots", side_effect=lambda: iter([root])),
                  patch.object(pe_runtime, "_SHARED_BONSAI_VISION_SHA256",
                               hashlib.sha256(fixture).hexdigest())):
                for name in shared_names:
                    with self.subTest(vision=name):
                        self.assertEqual(pick_mmproj(model.name, name), root / name)
                self.assertEqual(pick_mmproj(model.name, "Auto"), own)

    def test_bonsai_shared_projector_rejects_wrong_content_and_other_models(self):
        import pe_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vision = root / "Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf"
            vision.write_bytes(b"wrong projector")
            model = root / "Ternary-Bonsai-2-27B-Uncensored-Heretic-PQ2_0.gguf"
            other = root / "pe_i2i_heretic-Q4_K_M.gguf"
            for path in (model, other):
                path.write_bytes(b"model")
            with (patch("pe_runtime.model_roots", side_effect=lambda: iter([root])),
                  patch.object(pe_runtime, "_SHARED_BONSAI_VISION_SHA256",
                               hashlib.sha256(b"trusted Q8 projector").hexdigest())):
                with self.assertRaisesRegex(ValueError, "does not match"):
                    pick_mmproj(model.name, vision.name)
                vision.write_bytes(b"trusted Q8 projector")
                with self.assertRaisesRegex(ValueError, "does not match"):
                    pick_mmproj(other.name, vision.name)

    def test_projector_digest_cache_invalidates_same_size_atomic_replacement(self):
        import pe_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vision = root / "vision.gguf"
            vision.write_bytes(b"old")
            stat = vision.stat()
            replacement = root / "replacement.gguf"
            replacement.write_bytes(b"new")
            os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            pe_runtime._file_sha256.cache_clear()
            with patch.object(Path, "open", autospec=True, side_effect=Path.open) as opened:
                for _ in range(2):
                    self.assertEqual(pe_runtime._file_sha256(file_signature(vision)),
                                     hashlib.sha256(b"old").hexdigest())
                self.assertEqual(opened.call_count, 1)
                os.replace(replacement, vision)
                self.assertEqual(pe_runtime._file_sha256(file_signature(vision)),
                                 hashlib.sha256(b"new").hexdigest())
                self.assertEqual(opened.call_count, 2)
            pe_runtime._file_sha256.cache_clear()

    def test_runtime_discovery_selects_native_platform_executable(self):
        import pe_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            runtime.mkdir()
            for name in ("llama-server", "llama-server.exe"):
                (runtime / name).write_bytes(b"runtime")
            for platform, name in (("posix", "llama-server"), ("nt", "llama-server.exe")):
                with (self.subTest(platform=platform),
                      patch.object(pe_runtime, "ROOT", root),
                      patch.object(pe_runtime, "os", types.SimpleNamespace(name=platform, environ={}))):
                    self.assertEqual(LocalServer.binary(), runtime / name)

    def test_server_prefers_its_own_libraries_without_changing_parent_environment(self):
        import pe_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model.gguf"
            model.write_bytes(b"model")
            binary = root / "bundled" / "llama-server"
            process = MagicMock()
            process.poll.return_value = None
            health = MagicMock()
            health.status = 200
            health.__enter__.return_value = health
            inherited = "/shared/upstream-libraries:/another-directory"
            with (patch.object(pe_runtime, "ROOT", root),
                  patch.object(LocalServer, "binary", return_value=binary),
                  patch.object(pe_runtime.subprocess, "Popen", return_value=process) as launch,
                  patch.object(pe_runtime, "urlopen", return_value=health),
                  patch.dict(os.environ, {"LD_LIBRARY_PATH": inherited})):
                server = LocalServer()
                try:
                    server.start(model, None, 1024, 0)
                    environment = launch.call_args.kwargs["env"]
                    if os.name == "posix":
                        self.assertEqual(environment["LD_LIBRARY_PATH"],
                                         str(binary.resolve().parent) + os.pathsep + inherited)
                    self.assertEqual(os.environ["LD_LIBRARY_PATH"], inherited)
                finally:
                    server.stop()

    def test_file_signature_changes_for_same_size_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            replacement = Path(temp) / "replacement.gguf"
            path.write_bytes(b"old")
            before = file_signature(path)
            original = path.stat()
            replacement.write_bytes(b"new")
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            os.replace(replacement, path)
            self.assertNotEqual(file_signature(path), before)

    def test_server_cleans_up_after_failed_start_and_interrupt(self):
        class FakeProcess:
            def __init__(self, exited):
                self.exited = exited
                self.terminated = False
                self.returncode = 1 if exited else None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 0

            def wait(self, timeout):
                return self.returncode

        with tempfile.TemporaryDirectory() as temp:
            import pe_runtime
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"stub")
            exited = FakeProcess(True)
            with (patch.object(pe_runtime, "ROOT", Path(temp)),
                  patch.object(LocalServer, "binary", return_value=Path(temp) / "server"),
                  patch.object(pe_runtime.subprocess, "Popen", return_value=exited)):
                server = LocalServer()
                with self.assertRaisesRegex(RuntimeError, "exited"):
                    server.start(model, None, 1024, 0)
                self.assertIsNone(server.process)
                self.assertIsNone(server.profile)
                self.assertIsNone(server.log)

            interrupted = FakeProcess(False)
            comfy = types.ModuleType("comfy")
            memory = types.ModuleType("comfy.model_management")
            def interrupt():
                raise KeyboardInterrupt()
            memory.throw_exception_if_processing_interrupted = interrupt
            comfy.model_management = memory
            with (patch.object(pe_runtime, "ROOT", Path(temp)),
                  patch.object(LocalServer, "binary", return_value=Path(temp) / "server"),
                  patch.object(pe_runtime.subprocess, "Popen", return_value=interrupted),
                  patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": memory})):
                server = LocalServer()
                with self.assertRaises(KeyboardInterrupt):
                    server.start(model, None, 1024, 0)
                self.assertTrue(interrupted.terminated)
                self.assertIsNone(server.process)
                self.assertIsNone(server.log)

    def test_completion_uses_only_user_quoted_text_as_exact_literal(self):
        server = LocalServer()
        def response(answer):
            return {"choices": [{"message": {"content": json.dumps(answer)},
                                 "finish_reason": "stop"}], "usage": {}}
        edit_answer = {"rewritten_prompt": 'Blend "<image1>" with "<image2>".',
                       "wh_ratio": "4:3", "ratio_follow": ""}
        with patch.object(server, "_post_completion", return_value=response(edit_answer)):
            actual, _ = server.complete("edit", 'Blend "<image1>" with "<image2>".',
                                        ["image-a", "image-b"], 42, 1)
        self.assertEqual(actual["rewritten_prompt"], edit_answer["rewritten_prompt"])

        transparent_answer = {"rewritten_prompt": 'A poster reads "white background" in black letters.',
                              "wh_ratio": "4:5"}
        with patch.object(server, "_post_completion", return_value=response(transparent_answer)):
            actual, _ = server.complete("t2i", 'A poster reads "white background".',
                                        [], 42, 1, aspect_ratio="4:5", transparent_rgba=True)
        self.assertIn('"white background"', actual["rewritten_prompt"])

    def test_custom_system_prompt_replaces_template_and_keeps_node_contract(self):
        for task, images, answer in (
            ("t2i", [], {"rewritten_prompt": "A blue cat.", "wh_ratio": "1:1"}),
            ("edit", ["a", "b"], {"rewritten_prompt": "Blend <image1> into <image2>.",
                                  "wh_ratio": "", "ratio_follow": "<image2>"}),
        ):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as temp:
                server = LocalServer()
                custom = "  External rewriting policy.\nPreserve the requested edit.  "
                response = {"choices": [{"message": {"content": json.dumps(answer)},
                                          "finish_reason": "stop"}], "usage": {}}
                # No built-in template files exist: a supplied prompt replaces them.
                with (patch("pe_runtime.ROOT", Path(temp)),
                      patch.object(server, "_post_completion", return_value=response) as completion):
                    actual, info = server.complete(task, "Make the edit", images, 42, 1,
                                                   output_language="English", system_prompt=custom)
                system = completion.call_args.args[0]["messages"][0]["content"]
                self.assertTrue(system.startswith(custom.strip()))
                self.assertIn("Required node response format", system)
                self.assertIn("Runtime image-reference rule", system)
                self.assertIn("User-selected language override", system)
                expected = dict(answer)
                if task == "edit":
                    expected.update(wh_ratio="", ratio_follow="")
                    self.assertIn("one plain-text paragraph. No JSON", system)
                    self.assertNotIn("Set exactly one of wh_ratio", system)
                self.assertEqual(actual, expected)
                self.assertEqual(info["system_prompt_source"], "custom")
                self.assertEqual(info["system_prompt_sha256"],
                                 hashlib.sha256(custom.strip().encode()).hexdigest())
                self.assertEqual(info["effective_system_prompt_sha256"],
                                 hashlib.sha256(system.encode()).hexdigest())

    def test_empty_external_system_prompt_keeps_default_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "prompts").mkdir()
            for task in ("t2i", "edit"):
                name = "system_prompt_t2i.txt" if task == "t2i" else "system_prompt_edit_fast.txt"
                template = f"Built-in {task} policy.\r\nPreserve the image.\r\n"
                (root / "prompts" / name).write_bytes(template.encode())
                images = [] if task == "t2i" else ["a"]
                answer = {"rewritten_prompt": "A blue cat.", "wh_ratio": "1:1"}
                if task == "edit":
                    answer["ratio_follow"] = ""
                response = {"choices": [{"message": {"content": json.dumps(answer)},
                                          "finish_reason": "stop"}], "usage": {}}
                for custom in (None, "", " \n\t "):
                    with self.subTest(task=task, custom=custom):
                        server = LocalServer()
                        with (patch("pe_runtime.ROOT", root),
                              patch.object(server, "_post_completion", return_value=response) as completion):
                            _, info = server.complete(task, "Blue cat", images, 42, 1,
                                                      system_prompt=custom)
                        system = completion.call_args.args[0]["messages"][0]["content"]
                        self.assertTrue(system.startswith(template.replace("\r\n", "\n").strip()))
                        self.assertEqual("Required node response format" in system, task == "edit")
                        self.assertEqual(info["system_prompt_source"], "default")
                        self.assertEqual(info["system_prompt_sha256"],
                                         hashlib.sha256(template.encode()).hexdigest())

    def test_edit_accepts_plain_text_and_ignores_model_ratio_fields(self):
        prose = 'Remove the marked object and preserve the rest of the image.'
        variants = [prose, prose.replace(' and ', '\n and '),
                    '<think>hidden draft</think>\n' + prose,
                    '```text\n' + prose + '\n```']
        for ratio, follow in (("", ""), ("4:3", "<image9>"), ("bad", "bad")):
            variants.append(json.dumps({"rewritten_prompt": prose,
                                        "wh_ratio": ratio, "ratio_follow": follow}))
        variants.append(json.dumps({"rewritten_prompt": prose}))
        for raw in variants:
            with self.subTest(raw=raw):
                self.assertEqual(parse_edit_answer(raw),
                                 {"rewritten_prompt": prose, "wh_ratio": "", "ratio_follow": ""})
        for raw in ("", "<think>unfinished", '{"rewritten_prompt":',
                    '{"rewritten_prompt":42}', '{"answer":"missing prompt"}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_edit_answer(raw)

    def test_edit_extracts_pasted_chatml_system_and_preserves_plain_contract(self):
        policy = "Rewrite the edit as plain text; no JSON or ratio."
        custom = ("<|im_start|>system\n" + policy + "\n<|im_end|>\n"
                  "<|im_start|>user\n<|image_pad|>{编辑指令}<|im_end|>\n"
                  "<|im_start|>assistant\n<think>")
        text, source, digest = load_system_prompt("edit", custom)
        self.assertEqual((text, source), (policy, "custom"))
        self.assertEqual(digest, hashlib.sha256(policy.encode()).hexdigest())
        server = LocalServer()
        response = {"choices": [{"message": {"content": "Remove the marked object."},
                                 "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", return_value=response) as completion:
            answer, info = server.complete("edit", "移除红色区域内部的主体", ["a"], 42, 1,
                                           output_language="English", aspect_ratio="16:9", system_prompt=custom)
        payload = completion.call_args.args[0]
        system = payload["messages"][0]["content"]
        self.assertTrue(system.startswith(policy))
        for marker in ("<|im_start|>", "<|image_pad|>", "{编辑指令}", "<think>", "Set wh_ratio"):
            self.assertNotIn(marker, system)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(answer["rewritten_prompt"], "Remove the marked object.")
        self.assertEqual(info["format_retries"], 0)

    def test_edit_does_not_translate_literal_image_text_or_proper_names(self):
        server = LocalServer()
        prose = 'Remove the object beside the sign "夏日特惠" and preserve the label.'
        response = {"choices": [{"message": {"content": prose}, "finish_reason": "stop"}], "usage": {}}
        with (patch.object(server, "_post_completion", return_value=response) as completion,
              patch.object(server, "_translate_prose") as translate):
            answer, info = server.complete("edit", "移除标牌旁边的物体", ["a"], 42, 1,
                                           output_language="English")
        self.assertEqual(answer["rewritten_prompt"], prose)
        self.assertEqual(completion.call_count, 1)
        translate.assert_not_called()
        self.assertFalse(info["translation_fallback"])

    def test_edit_never_accepts_a_truncated_directive(self):
        server = LocalServer()
        requests = []
        def reply(payload, timeout):
            requests.append((payload["max_tokens"], payload["messages"][0]["content"]))
            return {"choices": [{"message": {"content": "unfinished"}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": payload["max_tokens"]}}
        with patch.object(server, "_post_completion", side_effect=reply):
            with self.assertRaisesRegex(ValueError, "also truncated"):
                server.complete("edit", "Remove the marked object", ["a"], 42, 1)
        self.assertEqual([limit for limit, _ in requests], [1536, 1536])
        self.assertIn("one plain-text paragraph", requests[1][1])
        self.assertNotIn("final JSON object", requests[1][1])

    def test_edit_budget_and_external_verbosity_are_caller_controlled(self):
        server = LocalServer()
        detailed = ("Rebuild the selected fabric region with continuous fine woven texture, "
                    "matching fold directions, gradual shading, and nearby reflected light. "
                    "Preserve the original seams and the visible outline of the hand. ") * 8
        for budget, policy in ((8192, "Write a detailed editing directive with all relevant visual details."),
                               (65536, "Keep this directive concise.")):
            with self.subTest(budget=budget):
                response = {"choices": [{"message": {"content": detailed}, "finish_reason": "stop"}],
                            "usage": {}}
                with patch.object(server, "_post_completion", return_value=response) as completion:
                    answer, info = server.complete("edit", "Rebuild the marked region", ["a"], 42, 1,
                                                   system_prompt=policy, edit_max_tokens=budget)
                payload = completion.call_args.args[0]
                system = payload["messages"][0]["content"]
                self.assertTrue(system.startswith(policy))
                self.assertNotIn("Keep the edit concise", system)
                self.assertEqual(payload["max_tokens"], budget)
                self.assertEqual(info["max_tokens"], budget)
                self.assertEqual(answer["rewritten_prompt"], detailed.strip())

    def test_edit_truncation_retry_respects_the_selected_budget(self):
        server = LocalServer()
        requests = []
        policy = "Describe every relevant texture, structure, and lighting detail."
        def reply(payload, timeout):
            requests.append(json.loads(json.dumps(payload)))
            content = "unfinished" if len(requests) == 1 else "Rebuild the fabric with matching folds."
            return {"choices": [{"message": {"content": content},
                                  "finish_reason": "length" if len(requests) == 1 else "stop"}],
                    "usage": {}}
        with patch.object(server, "_post_completion", side_effect=reply):
            answer, info = server.complete("edit", "Rebuild the marked region", ["a"], 42, 1,
                reasoning="on", system_prompt=policy, edit_max_tokens=8192)
        self.assertEqual([request["max_tokens"] for request in requests], [8192, 8192])
        self.assertEqual([request["chat_template_kwargs"]["enable_thinking"] for request in requests], [True, False])
        self.assertTrue(all(request["messages"][0]["content"].startswith(policy) for request in requests))
        self.assertEqual(info["max_tokens"], 8192)
        self.assertTrue(info["truncation_retry"])
        self.assertEqual(answer["rewritten_prompt"], "Rebuild the fabric with matching folds.")

    def test_invalid_edit_budgets_fail_before_completion(self):
        server = LocalServer()
        for budget in (0, -1, True, 1.5, "4096", 65537):
            with self.subTest(budget=budget), patch.object(server, "_post_completion") as completion:
                with self.assertRaisesRegex(ValueError, "edit_max_tokens"):
                    server.complete("edit", "Make the edit", ["a"], 42, 1, edit_max_tokens=budget)
                completion.assert_not_called()

    def test_text_to_image_budget_is_unaffected_by_edit_option(self):
        server = LocalServer()
        response = {"choices": [{"message": {"content": '{"rewritten_prompt":"A blue cat.","wh_ratio":"1:1"}'},
                                 "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", return_value=response) as completion:
            _, info = server.complete("t2i", "A blue cat", [], 42, 1, edit_max_tokens=1)
        self.assertEqual(completion.call_args.args[0]["max_tokens"], 16256)
        self.assertEqual(info["max_tokens"], 16256)

    def test_custom_system_prompt_preserves_validation_on_retry(self):
        server = LocalServer()
        systems = []
        def response(payload, timeout):
            systems.append(payload["messages"][0]["content"])
            prompt = ("Blend <image1>." if len(systems) == 1 else
                      "Blend <image1> into <image2>.")
            return {"choices": [{"message": {"content": json.dumps({
                "rewritten_prompt": prompt, "wh_ratio": "", "ratio_follow": "<image2>"})},
                "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", side_effect=response):
            _, info = server.complete("edit", "Blend the images", ["a", "b"], 42, 1,
                                      system_prompt="External blend policy.")
        self.assertEqual(info["format_retries"], 1)
        self.assertTrue(all(system.startswith("External blend policy.") for system in systems))
        self.assertIn("previous output failed validation", systems[1])
        self.assertEqual(info["system_prompt_source"], "custom")

    def test_bonsai_auto_disables_thinking_and_explicit_override_is_respected(self):
        cases = (
            ("Ternary-Bonsai-2-27B-PTQ1_0.gguf", "auto", False, 0.7, 0.8, 1.5),
            ("Ternary-Bonsai-2-27B-PQ2_0.gguf", "auto", False, 0.7, 0.8, 1.5),
            ("Ternary-Bonsai-2-27B-PQ2_0.gguf", "on", True, 1.0, 0.95, 0.0),
            (DEFAULT_EDIT, "auto", False, 1.0, 0.95, 0.0),
            (DEFAULT_EDIT, "off", False, 1.0, 0.95, 0.0),
        )
        for name, reasoning, thinking, temperature, top_p, penalty in cases:
            with self.subTest(name=name, reasoning=reasoning):
                server = LocalServer()
                server.profile = ((name, 0, 0), None, 49152, 99)
                response = {"choices": [{"message": {"content": json.dumps({
                    "rewritten_prompt": "Blend <image1> into <image2>.",
                    "wh_ratio": "", "ratio_follow": "<image2>"})}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 20}, "timings": {"prompt_ms": 100}}
                with patch.object(server, "_post_completion", return_value=response) as completion:
                    _, info = server.complete("edit", "Blend the images", ["a", "b"],
                                              42, 1, reasoning=reasoning)
                payload = completion.call_args.args[0]
                self.assertEqual(payload["chat_template_kwargs"]["enable_thinking"], thinking)
                self.assertEqual((payload["temperature"], payload["top_p"], payload["presence_penalty"]),
                                 (temperature, top_p, penalty))
                self.assertEqual(info["thinking_enabled"], thinking)
                self.assertEqual(payload["max_tokens"], 1536)
                self.assertEqual(info["response_format"], "plain_text")
                self.assertEqual(info["timings"], response["timings"])
                self.assertEqual(info["format_retries"], 0)

    def test_truncated_completion_retries_without_thinking_then_validates(self):
        server = LocalServer()
        server.profile = ("model", None, 24576, 99)
        requests = []
        def reply(payload, _timeout):
            requests.append((payload["max_tokens"],
                             payload["chat_template_kwargs"]["enable_thinking"],
                             payload["messages"][0]["content"]))
            if len(requests) == 1:
                return {"choices": [{"message": {"content": "unfinished"},
                                     "finish_reason": "length"}],
                        "usage": {"prompt_tokens": 3000, "completion_tokens": 16256}}
            return {"choices": [{"message": {"content": json.dumps({
                "rewritten_prompt": "A blue cat on a table.", "wh_ratio": "1:1"})},
                                 "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", side_effect=reply):
            answer, info = server.complete("t2i", "A blue cat", [], 42, 1)
        self.assertEqual(answer["rewritten_prompt"], "A blue cat on a table.")
        self.assertEqual([(limit, thinking) for limit, thinking, _ in requests],
                         [(16256, True), (16256, False)])
        self.assertIn("Return only the final JSON object", requests[1][2])
        self.assertTrue(info["truncation_retry"])
        self.assertEqual(info["format_retries"], 1)

        with patch.object(server, "_post_completion", return_value={
                "choices": [{"message": {"content": "unfinished"},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 3000, "completion_tokens": 16256}}) as completion:
            with self.assertRaisesRegex(ValueError, "retry without thinking was also truncated") as error:
                server.complete("t2i", "A blue cat", [], 42, 1)
        self.assertEqual(completion.call_count, 2)
        self.assertIn("context=24576", str(error.exception))
        self.assertIn("completion_tokens=16256", str(error.exception))

    def test_generic_image_tag_gets_image_count_specific_retry(self):
        cases = (
            ("t2i", [], {"rewritten_prompt": "Show <image> as a flower.", "wh_ratio": "1:1"},
             {"rewritten_prompt": "A red flower fills the frame.", "wh_ratio": "1:1"},
             "There are no input images"),
            ("edit", ["image-a", "image-b"],
             {"rewritten_prompt": "Blend <image> with <image2>.", "wh_ratio": "",
              "ratio_follow": "<image1>"},
             {"rewritten_prompt": "Blend <image1> with <image2>.", "wh_ratio": "",
              "ratio_follow": "<image1>"},
             "Do not use the unnumbered <image> tag"),
        )
        for task, images, invalid, valid, rule in cases:
            with self.subTest(task=task):
                server = LocalServer()
                requests = []
                def reply(payload, _timeout):
                    requests.append((payload["chat_template_kwargs"]["enable_thinking"],
                                     payload["messages"][0]["content"]))
                    answer = invalid if len(requests) == 1 else valid
                    return {"choices": [{"message": {"content": json.dumps(answer)},
                                         "finish_reason": "stop"}], "usage": {}}
                with patch.object(server, "_post_completion", side_effect=reply):
                    answer, info = server.complete(task, "Change the image.", images, 42, 1)
                expected = dict(valid)
                if task == "edit":
                    expected.update(wh_ratio="", ratio_follow="")
                self.assertEqual(answer, expected)
                self.assertEqual([thinking for thinking, _ in requests], [task == "t2i", False])
                self.assertIn(rule, requests[0][1])
                self.assertIn(rule, requests[1][1])
                self.assertIn("<image>", info["first_format_error"])
                self.assertEqual(info["format_retries"], 1)

    def test_single_image_bare_tag_is_normalized_without_retry(self):
        english, count = normalize_single_image_references(
            'Recolor <image> and leave the label "<image1>" untouched.')
        self.assertEqual(count, 1)
        self.assertEqual(english,
                         'Recolor <image1> and leave the label "<image1>" untouched.')
        chinese, count = normalize_single_image_references("把<image>中的猫改为蓝色。")
        self.assertEqual((chinese, count), ("把<image1>中的猫改为蓝色。", 1))
        server = LocalServer()
        response = {"choices": [{"message": {"content": json.dumps({
            "rewritten_prompt": "Change <image> to blue.", "wh_ratio": "",
            "ratio_follow": "<image1>"})}, "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", return_value=response) as completion:
            answer, info = server.complete("edit", "Change the image to blue.",
                                           ["image-data"], 42, 1)
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(answer["rewritten_prompt"], "Change <image1> to blue.")
        self.assertEqual(info["normalized_single_image_tags"], 1)
        self.assertEqual(info["format_retries"], 0)

    def test_translation_requires_exact_text_to_remain_quoted(self):
        server = LocalServer()
        result = {"choices": [{"message": {"content":
                  "HELLO appears elsewhere; the sign says 'GOODBYE'."},
                  "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "_post_completion", return_value=result):
            with self.assertRaisesRegex(ValueError, "quoted image text"):
                server._translate_prose("A sign says 'HELLO'.", "English", 42, 1)
        result["choices"][0]["message"]["content"] = '"HELLO" appears elsewhere; the sign says "GOODBYE".'
        with patch.object(server, "_post_completion", return_value=result):
            with self.assertRaisesRegex(ValueError, "quoted image text"):
                server._translate_prose("A sign says 'HELLO'.", "English", 42, 1)
        result["choices"][0]["message"]["content"] = 'The sign says "HELLO".'
        with patch.object(server, "_post_completion", return_value=result):
            translated, _ = server._translate_prose("A sign says 'HELLO'.", "English", 42, 1)
        self.assertEqual(translated, 'The sign says "HELLO".')

    def test_kept_server_reloads_replaced_model_file(self):
        class FakeProcess:
            returncode = None
            terminated = False

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 0

            def wait(self, timeout):
                return self.returncode

        with tempfile.TemporaryDirectory() as temp:
            import pe_runtime
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"old")
            first, second = FakeProcess(), FakeProcess()
            health = MagicMock()
            health.status = 200
            health.__enter__.return_value = health
            with (patch.object(pe_runtime, "ROOT", Path(temp)),
                  patch.object(LocalServer, "binary", return_value=Path(temp) / "server"),
                  patch.object(pe_runtime.subprocess, "Popen", side_effect=[first, second]) as launch,
                  patch.object(pe_runtime, "urlopen", return_value=health)):
                server = LocalServer()
                server.start(model, None, 1024, 0)
                server.start(model, None, 1024, 0)
                self.assertEqual(launch.call_count, 1)
                old_stat = model.stat()
                replacement = Path(temp) / "replacement.gguf"
                replacement.write_bytes(b"new")
                os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
                os.replace(replacement, model)
                server.start(model, None, 1024, 0)
                self.assertTrue(first.terminated)
                self.assertEqual(launch.call_count, 2)
                server.stop()


if __name__ == "__main__":
    unittest.main()
