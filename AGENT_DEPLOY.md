# Agent runbook — deploy MiniMax H3 to SaladCloud

Validated target date: **2026-09-24**

This runbook is for an automation agent. Goal: deploy the public image from this repo to SaladCloud with minimal experimentation.

## 1. Do not change these versions unless build logs force it

Primary runtime:

- Salad ComfyUI API: **1.19.2**
- ComfyUI: **0.35.0**
- PyTorch: **2.13.0**
- CUDA: **13.0**

Base image:

```text
ghcr.io/saladtechnologies/comfyui-api:comfy0.35.0-api1.19.2-torch2.13.0-cuda13.0-runtime
```

MiniMax H3 native support is already present in this ComfyUI generation.

## 2. Image published by this repo

GitHub Actions publishes:

```text
ghcr.io/gigantisimo/image:latest
```

If anonymous pulls fail, make the GHCR package Public before touching Salad configuration.

## 3. Models

The container uses Salad ComfyUI API's startup manifest.

Required FL2VA / T2V / I2V files:

```text
/opt/ComfyUI/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
/opt/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
/opt/ComfyUI/models/vae/minimax_h3_video_vae_int8_convrot.safetensors
/opt/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors
/opt/ComfyUI/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
```

Do not add Ref2VA weights during the first deployment. They increase startup download size and are not needed for basic T2V/I2V validation.

## 4. Salad container resources

First successful boot should prioritize compatibility over cost.

```text
GPU: RTX 5090 32 GB ONLY
Replicas: 1
vCPU: 8
RAM: 100 GB
Disk: 100 GB
Shared memory: 2048 MB
Allowed countries: All
```

Do not select RTX 3090 as a fallback in the same container group because this image/model selection is optimized for the 5090/Blackwell path.

Priority can be Medium/High for validation. After a successful benchmark, use Low/Lowest only when capacity is available.

## 5. Networking

Enable Container Gateway.

```text
Port: 3000
Authentication: Yes
Load balancer: Least Number Of Connections
Limit each server to one active request: Yes
Client timeout: 100000 ms
Server timeout: 100000 ms
```

The Salad ComfyUI wrapper listens on port 3000.

## 6. Probes

First boot:

```text
Startup Probe: Disabled
Liveness Probe: Disabled
Readiness Probe: Disabled
```

Reason: first boot downloads large model files. Do not let probes restart a healthy container while it is still downloading.

After one confirmed successful boot:

```text
Startup:   GET /health :3000
Readiness: GET /ready  :3000
```

## 7. Expected startup sequence

1. Salad pulls `ghcr.io/gigantisimo/image:latest`.
2. `comfyui-api` reads `/opt/h3/manifest.yaml`.
3. Required H3 model files are downloaded from public Hugging Face repositories.
4. ComfyUI starts.
5. Wrapper startup checks pass.
6. `GET /health` returns healthy.
7. `GET /ready` returns ready.
8. `GET /models` lists the H3 files.

If startup fails, inspect **Container Logs before changing any resource values**.

## 8. Smoke test order

Do not start with 15 seconds / native 768p.

First smoke test:

```text
5 seconds
864x480
24 fps
8 steps
Turbo LoRA enabled
```

Use:

```text
examples/h3_t2v_8step_api.json
```

Then test:

```text
examples/h3_i2v_8step_api.json
```

For I2V, replace the placeholder URL in the LoadImage node.

## 9. API request

The Salad wrapper expects:

```json
{
  "id": "job-id",
  "prompt": {},
  "webhook_v2": "https://your-webhook.example/h3"
}
```

Insert the whole example JSON as the value of `prompt`.

Use async requests with `webhook_v2`. Salad Container Gateway has a 100 second maximum timeout and H3 generation can exceed it.

For sequential I2V batches, use `/opt/h3/scripts/h3_i2v_batch_runner.py`. It sends one request at a time, receives `webhook_v2` completion locally, uploads each MP4 to a pre-signed S3 URL, and refreshes a status JSON and HTML gallery after every successful job. Prepare a private batch config with short-lived S3 GET/PUT URLs; never put storage credentials or signed URLs in this repository. The runner stops after its first failed job.

Keep `SaveVideo.filename_prefix` at the output root. The 1.19.2 wrapper reads saved outputs without resolving a non-empty ComfyUI subfolder.

## 10. Failure triage

### Image Not Found

Wrong image/tag or GHCR is not public.

Expected image:

```text
ghcr.io/gigantisimo/image:latest
```

### 401 / 403 pulling GHCR

Make the package Public in GitHub Packages.

### CUDA driver insufficient

The pinned Salad base uses CUDA 13 and requires a sufficiently recent NVIDIA driver. Reallocate to another RTX 5090 Salad node first. Do not randomly downgrade model files.

### Model missing

Check `manifest.yaml` and startup logs. `GET /models` should contain all five required files.

### Out of disk

Use at least 100 GB. Do not enable an aggressive LRU cache until baseline generation works.

### Out of host RAM

Keep 100 GB RAM during validation.

### Gateway timeout

Use `webhook_v2`; do not wait synchronously for a long video generation.

## 11. Quality progression

After the 5 second 8-step smoke test succeeds:

1. 5 s, 864x480, 8 steps.
2. 5 s, 1344x768, 8 steps.
3. 10 s, 1344x768, 8 steps.
4. 15 s, 1344x768, 8 steps.
5. Only then benchmark base/non-Turbo settings for maximum quality.

Record generation wall time, VRAM peak, host RAM peak and failed-job rate.

## 12. Authoritative upstream references

- MiniMax H3: https://github.com/MiniMax-AI/MiniMax-H3
- Salad ComfyUI API 1.19.2: https://github.com/SaladTechnologies/comfyui-api/releases/tag/1.19.2
- Native ComfyUI H3 workflows: https://github.com/Comfy-Org/workflow_templates
- Comfy H3 weights: https://huggingface.co/Comfy-Org/MiniMax-H3
- Turbo 8-step: https://huggingface.co/lightx2v/Minimax-h3-Turbo

## 13. Security

Repository is public. Never write credentials into this repo or Docker image.

Secrets belong in Salad environment variables or external secret storage.
