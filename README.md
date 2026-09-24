# MiniMax H3 on SaladCloud (RTX 5090)

Public deployment repo for running **MiniMax H3 locally on SaladCloud** with the official Salad ComfyUI API wrapper.

## What this image contains

The Docker image is intentionally small: **model weights are not baked into the image**. They are downloaded on container startup through Salad's `MANIFEST` mechanism into the normal ComfyUI model directories.

Pinned runtime used by this repo:

- Salad `comfyui-api` **1.19.2**
- ComfyUI **0.35.0**
- PyTorch **2.13.0**
- CUDA **13.0**
- Base image: `ghcr.io/saladtechnologies/comfyui-api:comfy0.35.0-api1.19.2-torch2.13.0-cuda13.0-runtime`

This is the current stable Salad runtime published with API 1.19.2.

## H3 models downloaded at startup

The default manifest is optimized for **FL2VA / T2V / I2V on RTX 5090**:

- `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- `minimax_h3_video_vae_int8_convrot.safetensors`
- `minimax_h3_audio_vae_fp32.safetensors`
- `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`

The 8-step Turbo LoRA is the default quality/speed target. The files and folder layout follow the current official ComfyUI MiniMax H3 templates.

## Published image

GitHub Actions builds and publishes:

```text
ghcr.io/gigantisimo/image:latest
```

A commit-specific tag is also published:

```text
ghcr.io/gigantisimo/image:<git-sha>
```

The repository contains **no API keys, wallets, Hugging Face tokens, Salad keys, or other secrets**.

> If Salad receives a 401/403 pulling the image, open the GitHub package page for `image` and make the package Public. GHCR package visibility is managed separately from repository visibility in some cases.

## Recommended SaladCloud configuration

Use only **RTX 5090 (32 GB)** for the first benchmark. Do not select 3090 and 5090 together.

```text
GPU: RTX 5090 32 GB
Replicas: 1
vCPU: 8
RAM: 100 GB
Disk: 100 GB
Shared memory: 2048 MB
Countries: All
```

Priority: use whatever currently has capacity. Medium/High is fine for the first validation run; switch to Low/Lowest later if available.

### Container image

Set Image Source to:

```text
ghcr.io/gigantisimo/image:latest
```

No registry credentials should be required once the GHCR package is public.

### Container Gateway

```text
Enabled: Yes
Port: 3000
Authentication: Yes
Load balancer: Least Number Of Connections
Limit each server to one active request: Yes
Client request timeout: 100000 ms
Server response timeout: 100000 ms
```

H3 jobs can exceed 100 seconds. For production, send `webhook_v2` or use Salad Job Queues instead of holding a synchronous request open.

### Probes

For the **first boot**, leave probes disabled so a long initial model download cannot cause a restart loop.

After the first successful run:

```text
Startup probe:   HTTP GET /health on port 3000
Readiness probe: HTTP GET /ready  on port 3000
Liveness probe:  optional
```

## First boot behavior

On first start the API wrapper reads `/opt/h3/manifest.yaml` and downloads all required H3 model files before starting ComfyUI. This can take time because the weights are large.

Watch Salad **Container Logs**. Do not stop/reallocate the instance while model downloads are in progress unless they have clearly failed.

Useful endpoints after startup:

```text
GET /health
GET /ready
GET /models
GET /docs
POST /prompt
```

## Example API prompts

- `examples/h3_t2v_8step_api.json` — 5 s, 864×480, 8-step Turbo, text-to-video
- `examples/h3_i2v_8step_api.json` — 5 s, 864×480, 8-step Turbo, image-to-video

The JSON files are the **ComfyUI API-format prompt graph**. Send them as the `prompt` field to Salad's ComfyUI API wrapper.

Example request shape:

```json
{
  "id": "your-job-id",
  "prompt": { "...": "contents of examples/h3_t2v_8step_api.json" },
  "webhook_v2": "https://your-service.example/h3-webhook"
}
```

Use a webhook for real H3 generations because the Salad Container Gateway max request timeout is 100 seconds.

## Quality mode

The included 8-step Turbo LoRA is intended as the default final-render compromise.

For maximum quality later, benchmark the stock/base H3 path at more steps without the Turbo LoRA. Do not assume a 4-step workflow is visually equivalent to 8-step or base inference.

## Important scope

This repo self-hosts **H3-Base at 768p-class resolutions**. MiniMax's hosted H3 Context-IR and Regenerate-2K pieces are separate hosted components and are not reproduced by this image.

## Sources

- MiniMax H3: https://github.com/MiniMax-AI/MiniMax-H3
- ComfyUI native H3 templates: https://github.com/Comfy-Org/workflow_templates
- Salad ComfyUI API: https://github.com/SaladTechnologies/comfyui-api
- H3 model files used by ComfyUI: https://huggingface.co/Comfy-Org/MiniMax-H3
- 8-step Turbo LoRA: https://huggingface.co/lightx2v/Minimax-h3-Turbo

## Security

This repository is public. **Never commit**:

- Salad API keys
- GitHub PATs
- wallet private keys / seed phrases
- S3/R2 credentials
- webhook secrets
- private model credentials

Use Salad Environment Variables / Secrets or GitHub Actions secrets when a secret is actually required.
