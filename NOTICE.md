# Third-party materials

This repository includes the Qwen Image 2.1 prompt-rewrite system prompt templates in `prompts/`. They originate from [QwenLM/Qwen-Image-2.1](https://github.com/QwenLM/Qwen-Image-2.1/tree/main/prompt_rewrite/prompts) and remain subject to the [Qwen Research License Agreement](LICENSE-QWEN-RESEARCH). The templates are included to run the corresponding Qwen prompt-enhancer checkpoints and are not relicensed as part of this node's own code.

Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT, Copyright (c) 2026 Hangzhou Tongyi Laboratory Technology Co., Ltd. All Rights Reserved.

The GGUF weights are **not** included in the GitHub or ComfyUI Registry package. The separate [Hugging Face mirror](https://huggingface.co/t8star/qwen-image-2.1-comfy) credits the original Qwen checkpoints, quantizers, and the Heretic derivative; its model card carries the applicable license and provenance.

The optional Heretic I2I GGUF and its vision projector are downloaded directly from [pottokao/Qwen-Image-2.1-PE-I2I-Heretic-GGUF](https://huggingface.co/pottokao/Qwen-Image-2.1-PE-I2I-Heretic-GGUF). That repository provides the model card, modification details, and Qwen Research License. This node does not modify or redistribute those weights.

The separate [Hugging Face model repository](https://huggingface.co/t8star/Qwen-Image-2.1-viggle-turbo-4step-r64-comfy) distributes a ComfyUI key-converted version of [Viggle's Qwen-Image-2.1 turbo LoRA](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo). Viggle trained the adapter; T8mars changed only the tensor key names. Its [NOTICE](https://huggingface.co/t8star/Qwen-Image-2.1-viggle-turbo-4step-r64-comfy/blob/main/NOTICE) gives the attribution, hashes, and modification notice. The LoRA remains under the Qwen Research License and is not included in this source tree or Registry package.

`prompts/system_prompt_edit_fast.txt` is a compact editing adaptation: it retains edit intent, image identity, reference tags and visible-text language rules, but outputs plain text, omits reasoning and ratio selection, and leaves the canvas under workflow control. The original official editing template is preserved in `system_prompt_edit.txt`.

The optional Bonsai Heretic PQ2 model comes from [OS-Software/Ternary-Bonsai-2-27B-Uncensored-Heretic-GGUF](https://huggingface.co/OS-Software/Ternary-Bonsai-2-27B-Uncensored-Heretic-GGUF). Its model card describes the derivative and upstream [PrismML Bonsai 2](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf). At revision `5ab50fd49410941a8d4f97912303b1c7d458ffa2`, its Q8 projector has the same SHA256 as the original Bonsai 2 Q8 projector; the node permits that verified shared component. Neither these weights nor the required PrismML runtime binaries are bundled here.
