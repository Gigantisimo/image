FROM ghcr.io/saladtechnologies/comfyui-api:comfy0.35.0-api1.19.2-torch2.13.0-cuda13.0-runtime

LABEL org.opencontainers.image.source="https://github.com/Gigantisimo/image"
LABEL org.opencontainers.image.description="MiniMax H3 FL2VA/T2V/I2V worker for SaladCloud RTX 5090"
LABEL org.opencontainers.image.licenses="MIT"

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# The current Salad base image contains huggingface_hub 1.16.1.
# Its `hf version` command is failing in this runtime, so comfyui-api does not
# detect the Hugging Face provider and falls back to its generic HTTP provider.
# Upgrade to the current CLI and verify it during image build so H3 model
# downloads use the dedicated Hugging Face/Xet path.
RUN uv pip install --system --no-cache-dir --upgrade "huggingface_hub[cli]==2.0.0" \
    && hf version

RUN mkdir -p /opt/h3 /opt/h3/examples /workflows

COPY manifest.yaml /opt/h3/manifest.yaml
COPY examples/ /opt/h3/examples/

ENV MANIFEST=/opt/h3/manifest.yaml
ENV PORT=3000
ENV HOST=::
ENV STARTUP_CHECK_INTERVAL_S=10
ENV STARTUP_CHECK_MAX_TRIES=180
ENV LRU_CACHE_SIZE_GB=0
ENV LOG_LEVEL=info

EXPOSE 3000
