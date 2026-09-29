import hashlib
import json
import math
from pathlib import Path
import re
import time

import torch

from .pe_runtime import (DEFAULT_EDIT, DEFAULT_EDIT_MAX_TOKENS, DEFAULT_T2I, MAX_EDIT_TOKENS,
                         SERVER, file_signature, load_system_prompt,
                         local_models, pick_mmproj, prepare_images, quoted_literals, resolve_model,
                         strip_quoted_literals, validate_edit_max_tokens)


ASPECT_RATIOS = ["auto", "1:1", "1:2", "2:3", "3:4", "4:5", "16:9",
                 "9:16", "21:9", "9:21", "5:4", "4:3", "2:1"]


def _resolved_language(selection, rewritten_prompt, user_prompt=None):
    if selection == "中文":
        return "zh"
    if selection == "English":
        return "en"
    exact_literals = set(quoted_literals(user_prompt)) if user_prompt is not None else None
    prose = strip_quoted_literals(rewritten_prompt, exact_literals)
    return "zh" if re.search(r"[\u4e00-\u9fff]", prose) else "en"


def _format_prompt(prompt, transparent_rgba, language):
    pieces = []
    if transparent_rgba:
        pieces.append("这是一张带有透明度的RGBA图像。" if language == "zh"
                      else "This is an RGBA image with transparency. ")
    body = prompt.strip()
    if transparent_rgba and body and body[-1] not in ".!?。！？":
        body += "。" if language == "zh" else "."
    pieces.append(body)
    if transparent_rgba:
        pieces.append("该图像具有alpha通道，背景是透明的。" if language == "zh"
                      else " The image has an alpha channel, and the background is transparent.")
    return "".join(pieces)


def _choices(vision=False):
    names = list(local_models(vision))
    if vision:
        return ["Auto"] + names
    return names or ["(no local GGUF found)"]


class QwenPERewrite:
    @classmethod
    def INPUT_TYPES(cls):
        models = _choices()
        images = {f"image_{i}": ("IMAGE",) for i in range(1, 11)}
        return {
            "required": {
                "user_prompt": ("STRING", {"multiline": True, "default": ""}),
                "task": (["auto", "t2i", "edit"], {"default": "auto"}),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "auto",
                                                 "tooltip": "文生图使用模型建议或指定比例；图片编辑始终保持 canvas_image 参考图比例。"}),
                "output_language": (["auto", "中文", "English"], {"default": "auto",
                                                                    "tooltip": "控制改写描述的语言；图内原文保留用户指定文字。"}),
                "transparent_rgba": ("BOOLEAN", {"default": False,
                                                    "tooltip": "将 RGBA、alpha 通道和透明背景要求加入最终提示词。"}),
                "t2i_model": (models, {"default": DEFAULT_T2I if DEFAULT_T2I in models else models[0]}),
                "edit_model": (models, {"default": DEFAULT_EDIT if DEFAULT_EDIT in models else models[0]}),
                "vision_model": (_choices(True), {"default": "Auto"}),
                "model_lifetime": (["after_run", "keep_loaded"], {"default": "after_run"}),
                "seed": ("INT", {"default": 42, "min": 0, "max": 0x7FFFFFFF}),
            },
            "optional": {
                **images,
                "reasoning": (["auto", "off", "on"], {"default": "auto",
                              "tooltip": "auto: 图片编辑与 Bonsai 默认关闭思考；off/on 显式控制。"}),
                "system_prompt": ("STRING", {"forceInput": True,
                                  "tooltip": "外接系统提示词替换默认模板；空文本回退默认。编辑只输出纯文本；粘贴 ChatML 时仅提取 system 正文。"}),
                "canvas_image": ("INT", {"default": 1, "min": 1, "max": 10,
                                  "tooltip": "图片编辑固定沿用哪张参考图的画布比例，按实际有图的输入顺序编号。"}),
                "edit_max_tokens": ("INT", {"default": DEFAULT_EDIT_MAX_TOKENS, "min": 1,
                                          "max": MAX_EDIT_TOKENS,
                                          "tooltip": "图片编辑的生成 token 上限（含启用的思考）；重试也遵守此值。详略由 system_prompt 控制，实际长度仍受模型上下文剩余空间限制。"}),
            },
        }

    RETURN_TYPES = ("STRING", "PE_RESULT", "STRING")
    RETURN_NAMES = ("rewritten_prompt", "pe_result", "diagnostics")
    FUNCTION = "rewrite"
    CATEGORY = "Qwen Image 2.1/Prompt Rewrite"

    @classmethod
    def IS_CHANGED(cls, task, t2i_model, edit_model, vision_model, **kwargs):
        fingerprint = hashlib.sha256()
        root = Path(__file__).resolve().parent
        fingerprint.update((root / "pe_nodes.py").read_bytes())
        fingerprint.update((root / "pe_runtime.py").read_bytes())
        for template in ("system_prompt_t2i.txt", "system_prompt_edit_fast.txt"):
            fingerprint.update((root / "prompts" / template).read_bytes())
        # Comfy calls IS_CHANGED before resolving linked IMAGE outputs. Linked
        # values arrive as None here, so auto must include both possible models.
        names = [t2i_model, edit_model] if task == "auto" else [t2i_model if task == "t2i" else edit_model]
        for name in names:
            try:
                path = resolve_model(name)
            except FileNotFoundError:
                if task != "auto":
                    raise
                fingerprint.update(f"unresolved-model:{name}".encode())
                continue
            fingerprint.update(repr(file_signature(path)).encode())
        if task in ("auto", "edit"):
            try:
                path = pick_mmproj(edit_model, vision_model)
            except (FileNotFoundError, ValueError):
                if task == "edit":
                    raise
                fingerprint.update(f"unresolved-vision:{edit_model}:{vision_model}".encode())
            else:
                fingerprint.update(repr(file_signature(path)).encode())
        return fingerprint.hexdigest()

    def rewrite(self, user_prompt, task, aspect_ratio, output_language, transparent_rgba,
                t2i_model, edit_model, vision_model,
                model_lifetime, seed, reasoning="auto", system_prompt=None, canvas_image=1,
                edit_max_tokens=DEFAULT_EDIT_MAX_TOKENS, **kwargs):
        if not user_prompt.strip():
            raise ValueError("Enter a text instruction; image-only requests need an explicit editing goal")
        present = sorted((int(key.split("_")[-1]), value) for key, value in kwargs.items() if value is not None)
        images = [value for _, value in present]
        if len(images) > 10:
            raise ValueError("At most 10 reference images are supported")
        actual_task = ("edit" if images else "t2i") if task == "auto" else task
        if actual_task == "edit":
            edit_max_tokens = validate_edit_max_tokens(edit_max_tokens)
        if actual_task == "edit" and not images:
            raise ValueError("edit requires at least one image")
        if actual_task == "t2i" and images:
            raise ValueError("t2i cannot receive images; use edit for image-conditioned generation")
        if actual_task == "edit" and (not isinstance(canvas_image, int) or isinstance(canvas_image, bool)
                                       or not 1 <= canvas_image <= len(images)):
            raise ValueError("canvas_image must select an available input image")
        _, template_source, template_hash = load_system_prompt(actual_task, system_prompt)
        model_name = t2i_model if actual_task == "t2i" else edit_model
        model = resolve_model(model_name)
        mmproj = pick_mmproj(model_name, vision_model) if actual_task == "edit" else None
        encoded, dimensions = prepare_images(images)
        image_fingerprints = [hashlib.sha256(value.encode("ascii")).hexdigest() for value in encoded]
        context = 24576 if not images else (49152 if len(images) <= 5 else 65536)
        try:
            import comfy.model_management as memory
            memory.free_memory(12 * 1024**3, memory.get_torch_device())
            memory.soft_empty_cache()
        except ImportError:
            pass
        started = time.monotonic()
        with SERVER.lock:
            try:
                SERVER.start(model, mmproj, context, 99)
                server_ready = time.monotonic()
                answer, info = SERVER.complete(actual_task, user_prompt, encoded, seed, 900,
                                               output_language, aspect_ratio, transparent_rgba,
                                               reasoning=reasoning, system_prompt=system_prompt,
                                               edit_max_tokens=edit_max_tokens)
            finally:
                if model_lifetime == "after_run":
                    SERVER.stop()
        model_wh_ratio = answer["wh_ratio"]
        model_ratio_follow = answer.get("ratio_follow", "")
        if actual_task == "edit":
            answer["wh_ratio"] = ""
            answer["ratio_follow"] = f"<image{canvas_image}>"
        elif aspect_ratio != "auto":
            answer["wh_ratio"] = aspect_ratio
            answer["ratio_follow"] = ""
        language = _resolved_language(output_language, answer["rewritten_prompt"], user_prompt)
        final_prompt = _format_prompt(answer["rewritten_prompt"], transparent_rgba, language)
        result = {
            "task": actual_task,
            "rewritten_prompt": final_prompt,
            "wh_ratio": answer["wh_ratio"],
            "ratio_follow": answer.get("ratio_follow", ""),
            "model_wh_ratio": model_wh_ratio,
            "model_ratio_follow": model_ratio_follow,
            "selected_aspect_ratio": "auto" if actual_task == "edit" else aspect_ratio,
            "canvas_image": canvas_image if actual_task == "edit" else None,
            "response_format": info.get("response_format"),
            "output_language": language,
            "transparent_rgba": transparent_rgba,
            "image_dimensions": dimensions,
            "image_input_ports": [f"image_{index}" for index, _ in present],
            "image_fingerprints": image_fingerprints,
            "model": model.name,
            "mmproj": mmproj.name if mmproj else "",
            "system_prompt_source": template_source,
            "system_prompt_sha256": template_hash,
            "effective_system_prompt_sha256": info.get("effective_system_prompt_sha256"),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "server_startup_seconds": round(server_ready - started, 2),
            "reasoning": info.get("reasoning", reasoning),
            "thinking_enabled": info.get("thinking_enabled"),
            "edit_max_tokens": edit_max_tokens if actual_task == "edit" else None,
            "max_tokens": info.get("max_tokens"),
            "timings": info.get("timings"),
            "seed": seed,
            "finish_reason": info["finish_reason"],
            "usage": info["usage"],
            "format_retries": info.get("format_retries", 0),
            "first_format_error": info.get("first_format_error"),
            "truncation_retry": info.get("truncation_retry", False),
            "normalized_single_image_tags": info.get("normalized_single_image_tags", 0),
            "removed_background_sentences": info.get("removed_background_sentences", 0),
            "normalized_background_phrases": info.get("normalized_background_phrases", 0),
            "normalized_margin_phrases": info.get("normalized_margin_phrases", 0),
            "translation_fallback": info.get("translation_fallback", False),
            "translation_usage": info.get("translation_usage"),
        }
        diagnostics = json.dumps({key: value for key, value in result.items() if key != "rewritten_prompt"},
                                 ensure_ascii=False)
        return result["rewritten_prompt"], result, diagnostics


class QwenPECanvas:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pe_result": ("PE_RESULT",),
            "resolution": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 32,
                                   "tooltip": "画布像素预算为 resolution²；跟随原图尺寸时，大图会等比缩至预算内。"}),
            "follow_input_size": ("BOOLEAN", {"default": True}),
        }}

    RETURN_TYPES = ("INT", "INT", "LATENT", "STRING")
    RETURN_NAMES = ("width", "height", "latent", "ratio_source")
    FUNCTION = "canvas"
    CATEGORY = "Qwen Image 2.1/Prompt Rewrite"

    def canvas(self, pe_result, resolution, follow_input_size):
        follow = pe_result["ratio_follow"]
        if follow:
            index = int(follow.removeprefix("<image").removesuffix(">")) - 1
            width, height = pe_result["image_dimensions"][index]
            source = follow
        else:
            left, right = map(int, pe_result["wh_ratio"].split(":"))
            ratio = left / right
            width = math.sqrt(resolution * resolution * ratio)
            height = math.sqrt(resolution * resolution / ratio)
            source = pe_result["wh_ratio"]
        if follow and not follow_input_size:
            ratio = width / height
            width = math.sqrt(resolution * resolution * ratio)
            height = math.sqrt(resolution * resolution / ratio)
        scale = min(1.0, resolution / math.sqrt(width * height), 4096 / max(width, height))
        width *= scale
        height *= scale
        if min(width, height) < 16:
            upscale = 16 / min(width, height)
            if max(width, height) * upscale > 4096 or width * height * upscale**2 > resolution**2:
                raise ValueError("aspect ratio is too extreme for the selected canvas budget")
            width *= upscale
            height *= upscale
        # Flooring to the required 16-pixel grid keeps scaled input canvases
        # inside the selected pixel budget instead of rounding back above it.
        width = max(16, math.floor(width / 16) * 16)
        height = max(16, math.floor(height / 16) * 16)
        import comfy.model_management as memory
        latent = torch.zeros([1, 64, height // 16, width // 16], device=memory.intermediate_device())
        return width, height, {"samples": latent}, source


class QwenPEUnload:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"pe_result": ("PE_RESULT",)}}

    RETURN_TYPES = ("PE_RESULT",)
    RETURN_NAMES = ("pe_result",)
    FUNCTION = "unload"
    CATEGORY = "Qwen Image 2.1/Prompt Rewrite"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def unload(self, pe_result):
        SERVER.stop()
        return (pe_result,)


class QwenPEModelList:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"refresh": ("BOOLEAN", {"default": False})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("local_models",)
    FUNCTION = "list_models"
    CATEGORY = "Qwen Image 2.1/Prompt Rewrite"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def list_models(self, refresh):
        models = local_models()
        vision = local_models(True)
        return (json.dumps({"models": list(models), "vision_models": list(vision)}, ensure_ascii=False, indent=2),)


NODE_CLASS_MAPPINGS = {
    "QwenPERewriteT8": QwenPERewrite,
    "QwenPECanvasT8": QwenPECanvas,
    "QwenPEUnloadT8": QwenPEUnload,
    "QwenPEModelListT8": QwenPEModelList,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "QwenPERewriteT8": "Qwen Image 2.1 PE Rewrite T8",
    "QwenPECanvasT8": "Qwen Image 2.1 PE Canvas T8",
    "QwenPEUnloadT8": "Qwen Image 2.1 PE Unload T8",
    "QwenPEModelListT8": "Qwen Image 2.1 PE Local Models T8",
}
