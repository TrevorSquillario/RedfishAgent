# Start from a bare CUDA devel image to avoid pre-installed Torch conflicts
FROM nvidia/cuda:13.1-devel-ubuntu24.04

# Set non-interactive and environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    # Blackwell-specific runtime flags
    VLLM_USE_FLASHINFER_MOE_FP8=1 \
    VLLM_FLASHINFER_MOE_BACKEND=throughput \
    # Force vLLM to see the Blackwell arch (GB10 is sm_121 or sm_100 depending on variant)
    TORCH_CUDA_ARCH_LIST="12.0;12.1;10.0"

# Install Python 3.12 and build essentials
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3-pip \
    ninja-build \
    git \
    && rm -rf /var/lib/apt/lists/*

# Link python to python3
RUN ln -s /usr/bin/python3.12 /usr/bin/python

# Step 1: Install the exact version of Torch matching the vLLM 0.18.0 wheels
# Using the cu131 index ensures compatibility with your Blackwell driver (580+)
RUN pip install --upgrade pip && \
    pip install uv && \
    uv pip install torch==2.10.0 torchvision --index-url https://download.pytorch.org/whl/cu131

# Step 2: Install vLLM using `uv` wrapper and let it select the torch backend
RUN uv pip install vllm --torch-backend=auto

# Optional: Fix for libcudart.so.12 pathing issues in multi-process vLLM forks (CUDA 13 specific)
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Expose API port
EXPOSE 8000
WORKDIR /models

# Start the server with the Nemotron-Cascade settings
# Using --trust-remote-code for the custom Nemotron reasoning-parser
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--model", "nvidia/Nemotron-Cascade-2-30B-A3B", "--host", "0.0.0.0"]