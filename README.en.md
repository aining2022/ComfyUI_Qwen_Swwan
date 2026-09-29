# Qwen Image 2.1 Prompt Rewrite T8

**Language / 语言: [简体中文](README.md) · English**

A local prompt rewriting node for ComfyUI. Give it text alone, or text plus 1–10 reference images. It selects the corresponding Qwen Image 2.1 PE GGUF model and returns a prompt for text-to-image generation or image editing. It supports Chinese and English output, aspect ratios, transparent-background instructions, and model unloading.

> **What the models do:** The GGUF files in this project **rewrite prompts**. To generate images, your ComfyUI workflow also needs a Qwen Image 2.1 diffusion model, text encoder, and VAE. Those are separate downloads.

[GGUF model mirror](https://huggingface.co/t8star/qwen-image-2.1-comfy) · [ComfyUI Registry page](https://registry.comfy.org/zh/publishers/t8star/nodes/qwen-image-prompt-rewrite-t8) · [Example workflows](workflows/) · [Report an issue](https://github.com/T8mars/Comfyui-Qwen-Image-Prompt-Rewrite-T8/issues/new)

## Quick setup

1. Place the repository under `ComfyUI/custom_nodes/Comfyui-Qwen-Image-Prompt-Rewrite-T8/`, then restart ComfyUI:

   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/T8mars/Comfyui-Qwen-Image-Prompt-Rewrite-T8.git
   cd Comfyui-Qwen-Image-Prompt-Rewrite-T8
   ```

2. From the node directory, use the **same Python environment as ComfyUI** to download four Q4_K_M main models and two I2I vision components:

   ```bash
   python tools/download_models.py
   ```

   The downloader verifies file sizes and SHA256 hashes and resumes interrupted downloads. You can also get the six files from the [base model mirror](https://huggingface.co/t8star/qwen-image-2.1-comfy) and the [Heretic I2I source repository](https://huggingface.co/pottokao/Qwen-Image-2.1-PE-I2I-Heretic-GGUF), then place them, with their original names, in `models/llm/qwenimage-pe/`:

   | Purpose | File |
   | --- | --- |
   | Text-to-image | `Qwen-Image-2.1-PE-T2I.Q4_K_M.gguf` |
   | Image editing | `Qwen-Image-2.1-PE-I2I.Q4_K_M.gguf` |
   | Vision component for editing | `Qwen-Image-2.1-PE-I2I.mmproj-bf16.gguf` |
   | Optional Heretic text-to-image model | `pe_t2i_heretic-Q4_K_M.gguf` |
   | Optional Heretic image-editing model | `pe_i2i_heretic-Q4_K_M.gguf` |
   | Vision component for Heretic editing | `pe_i2i_heretic.mmproj-bf16.gguf` |

   The source repository does not provide a Q4 vision component, so this project uses the BF16 file. You may also use `ComfyUI/models/llm/qwenimage-pe/` or set `QWEN_PE_MODEL_DIR` to a custom model directory. Model weights are not bundled with the GitHub source or Registry package.

3. Install a compatible `llama-server`. On Windows, run this from the node directory:

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools/download_runtime.ps1
   ```

   This script downloads a llama.cpp Windows CUDA 12.4 runtime. On other platforms, install a compatible `llama-server` yourself and set `QWEN_PE_LLAMA_SERVER` to its executable path. ComfyUI's Python environment must contain `torch`, `numpy`, and `Pillow`.

After restarting ComfyUI, find the nodes under **Qwen Image 2.1 / Prompt Rewrite**. Registry availability may change; use the repository installation steps above if the node is not offered in Manager.

## Using the nodes

| Node | Purpose |
| --- | --- |
| **PE Rewrite T8** | Rewrite a prompt and return text, a structured `PE_RESULT`, and diagnostics JSON. |
| **PE Canvas T8** | Editing follows a fixed reference image; text-to-image uses an aspect ratio to create width, height, and an empty Qwen Image 2.1 latent. |
| **PE Unload T8** | Explicitly stop a model retained with `keep_loaded`. |
| **PE Local Models T8** | List discovered main GGUF models and vision components. |

Enter your instruction in `user_prompt` and choose the following options in **PE Rewrite T8**:

- `task`: `auto` selects text-to-image or editing from the images **actually present**. `t2i` accepts text only; `edit` requires at least one image.
- `t2i_model` / `edit_model`: Separate model choices for the two tasks. Select `pe_i2i_heretic-Q4_K_M.gguf` in `edit_model` to use Heretic editing; `vision_model=Auto` selects its matching vision component.
- `aspect_ratio`: Applies to text-to-image only; `auto` uses the model suggestion and an explicit choice overrides it. Editing retains the ratio of the `canvas_image` reference.
- `output_language`: `auto`, `中文`, or `English` guides descriptive prose. Visible image text is preserved separately. Editing avoids additional translation calls solely for that text or proper names.
- `transparent_rgba`: Adds RGBA, alpha-channel, and transparent-background instructions to the final prompt. **This constrains the prompt; it does not guarantee that the downstream image has an alpha channel.**
- `model_lifetime`: `after_run` unloads after success or failure. `keep_loaded` speeds up repeated calls; use **PE Unload T8** when finished.
- `reasoning`: `auto` disables thinking for editing and Bonsai 2; `off` / `on` override it. Keep `auto` or `off` for fast editing.
- `system_prompt`: Optional external STRING input, such as **Primitive String Multiline**. Nonempty text replaces the built-in template; blank input uses the default. Editing uses a compact plain-text template. The node appends its plain-text response and image-reference rules. A pasted ChatML conversation is reduced to its system body. Diagnostics identify the source and SHA256.
- `edit_max_tokens`: Maximum generated tokens for editing, default `1536`, configurable from `1` to `65536` or through a converted input. This is a cap, not a target length; enabled thinking shares the budget. Truncation retries retain the same cap, and remaining model context can limit output earlier. Control verbosity through `system_prompt`; the node no longer appends a brevity instruction. Older workflows use the default, and text-to-image behavior is unchanged.
- `canvas_image`: Editing canvas reference number, default `1`; choose `2`, etc. for multiple inputs, numbered by images actually present. The node retains that image ratio without asking the model. Workflows using only the rewritten text keep their existing downstream canvas controls.

Each `image_1` through `image_10` input accepts one IMAGE, not a batch. Inputs returning `None` are skipped; remaining images are numbered in port order. For example, if `image_1` has an image, `image_2` is `None`, and `image_9` has an image, their prompt references are `<image1>` and `<image2>`. `PE_RESULT.image_input_ports` records the original ports. A single-image rewrite may describe the image naturally or use `<image1>`; a multi-image rewrite must include every tag from `<image1>` through `<imageN>`. If you connect the output to `TextEncodeQwenImage21`, give that node the same images in the same order.

### Using Bonsai 2 27B

The node also supports `Ternary-Bonsai-2-27B-PTQ1_0.gguf` / `PQ2_0`, paired with `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` for image editing. Put the files in the model directory above and select Bonsai in `t2i_model` / `edit_model`. The matching vision component can be selected automatically or explicitly.

The node also supports [OS-Software's Heretic PQ2](https://huggingface.co/OS-Software/Ternary-Bonsai-2-27B-Uncensored-Heretic-GGUF), `Ternary-Bonsai-2-27B-Uncensored-Heretic-PQ2_0.gguf`; both Bonsai workflows below select this model. It accepts the original `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` or an explicitly selected upstream `mmproj-Q8_0.gguf`. Pairing across filename prefixes verifies the known Q8 SHA256 and caches it by file signature. `Auto` selects a vision file with the Heretic model prefix in the same directory.

Bonsai 2 requires [PrismML's llama.cpp build](https://github.com/PrismML-Eng/Bonsai-demo/blob/main/BACKEND-SUPPORT.md). On Linux, set `QWEN_PE_LLAMA_SERVER` or place the native `llama-server` under the node's `runtime/` directory, keeping its bundled shared libraries. The Linux subprocess prioritizes libraries beside that executable so another llama.cpp build in Conda cannot override it. Use `model_lifetime=after_run` to release GPU memory after prompt rewriting.

Prompt enhancement processes a long system prompt and image inputs. The [official model card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf#choosing-a-packing) recommends `PQ2_0` for faster prompt processing; `PTQ1_0` decodes faster on Ada GPUs. This example selects `PQ2_0`, `reasoning=off`, and `model_lifetime=after_run`.

The [two-image editing workflow](workflows/Qwen-PE-2.1-Bonsai-Edit-2-Images.json) includes Bonsai rewriting, Qwen Image 2.1 encoding, sampling, and saving. Its demo images refer to inputs on the validation server; replace them in the two `LoadImage` nodes as needed.

The [external system prompt version](workflows/Qwen-PE-2.1-Bonsai-Edit-2-Images-External-System-Prompt.json) connects a built-in `PrimitiveStringMultiline` node, initially containing the compact editing template for you to change. The two-image demo fixes `canvas_image=2` to retain the scene image ratio.

## Example workflows

Drag a JSON file from [workflows/](workflows/) onto the ComfyUI canvas, or place it in your ComfyUI workflows directory. Start with one of these:

| Scenario | Workflow | Extra requirement |
| --- | --- | --- |
| Rewrite a text-to-image prompt | [Text-to-Image-Ready](workflows/Qwen-PE-2.1-Text-to-Image-Ready.json) | PE GGUF and llama-server |
| Rewrite a two-image edit | [Edit-2-Images-Ready](workflows/Qwen-PE-2.1-Edit-2-Images-Ready.json) | Choose files in both `LoadImage` nodes |
| Generate a complete text-to-image result | [Full-T2I](workflows/Qwen-PE-2.1-Full-T2I.json) | Separate diffusion model, text encoder, and VAE |
| Generate a complete two-image edit | [Full-Edit-2-Images](workflows/Qwen-PE-2.1-Full-Edit-2-Images.json) | Same; reselect the reference images |
| Generate a complete ten-image edit | [Full-Edit-10-Images](workflows/Qwen-PE-2.1-Full-Edit-10-Images.json) | Same; example uses built-in color images |

The directory also has Heretic text-to-image, Chinese and English transparency, Chinese output, model listing, and explicit unload examples. Full generation workflows require a recent ComfyUI with `TextEncodeQwenImage21` and the separate [Qwen Image 2.1 generation models](https://huggingface.co/Comfy-Org/Qwen-Image-2.1). The full two-image example includes [fixtures](workflows/fixtures/); when importing it into another ComfyUI installation, copy the images to its `input/` directory or reselect them in `LoadImage`. The included 512-pixel, 12-step settings are quick-run examples.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Empty model list or GGUF not found | Check the filenames and model directory. **PE Local Models T8** shows what the node discovered. |
| Editing reports a missing mmproj | Put the matching I2I main model and vision file together, or choose the matching `vision_model`. |
| `model response reached the generation or context limit` | The node retries once without thinking and retains the token cap. For editing, increase `edit_max_tokens` if needed, or reduce image/text inputs when context space is exhausted. Multi-image jobs use more VRAM. |
| `model failed format validation` | Both attempts failed the edit text/image-reference checks, or the text-to-image JSON, ratio, or language checks. Read the error details. After a successful run, `diagnostics` can confirm the task and input-port mapping. |
| `TextEncodeQwenImage21` is missing | Update to a ComfyUI release supporting Qwen Image 2.1. Prompt-only workflows do not need this node. |
| The output PNG is still opaque | Check the downstream image model, VAE, and saving workflow. This node only writes the transparency prompt. |

The local `llama-server` listens on `127.0.0.1` only. A visual copy of each input is scaled to at most about one million pixels and a 4096-pixel longest side; PE Canvas retains the original dimensions. Editing accepts one plain-text paragraph; legacy JSON is tolerated by extracting `rewritten_prompt` and ignoring model ratio fields. `canvas_image` fixes the reference ratio, and `aspect_ratio` has no effect in editing. Editing defaults to no thinking, avoids extra translation calls for image text/proper names, and checks nonempty, complete output, image references and transparency constraints with at most one retry. Text-to-image retains its JSON, language, and aspect-ratio validation.

## Optional: Viggle Turbo 4-step LoRA

[Download the ComfyUI-converted LoRA](https://huggingface.co/t8star/Qwen-Image-2.1-viggle-turbo-4step-r64-comfy/resolve/main/Qwen-Image-2.1-viggle-turbo-4step-r64-comfyui-T8.safetensors?download=true). This LoRA modifies the **image-generation diffusion model** and is installed separately from the PE GGUF files. This repository only converted the key names of [Viggle's original LoRA](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo); it did not retrain the weights. Put the file in `ComfyUI/models/loras/`, add `LoraLoaderModelOnly` after the base model, and set `strength_model=1.0`. Follow the original model's **4 steps, CFG 1.0, and empty negative prompt** settings; change the 12-step setting in the full example workflows when using this LoRA. See the [Hugging Face model card](https://huggingface.co/t8star/Qwen-Image-2.1-viggle-turbo-4step-r64-comfy) for conversion and verification details. This converted model has not undergone a separate end-to-end image-generation test.

## Sources and license

The system prompt templates come from the [official Qwen Image 2.1 repository](https://github.com/QwenLM/Qwen-Image-2.1/tree/main/prompt_rewrite/prompts). See [NOTICE.md](NOTICE.md) for provenance and modifications. The relevant templates, models, and optional LoRA are subject to the [Qwen Research License Agreement](LICENSE-QWEN-RESEARCH): non-commercial research or evaluation only; commercial use requires separate permission from the rights holder.

## T8star

[Bilibili](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [Free gallery](https://www.openzhenzhen.com) · [Online AI apps](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI package](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
