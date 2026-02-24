# ============================================
# Stage 1: Build PJSIP from source
# ============================================
FROM nvidia/cuda:12.2.2-devel-ubuntu22.04 AS pjsip-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3 python3-dev python3-pip \
    swig \
    wget \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Build PJSIP 2.16 with Python bindings
RUN cd /tmp && \
    wget -q https://github.com/pjsip/pjproject/archive/refs/tags/2.16.tar.gz && \
    tar -xzf 2.16.tar.gz && \
    cd pjproject-2.16 && \
    ./configure \
        --prefix=/usr \
        --enable-shared \
        --with-external-srtp=no \
        CFLAGS="-fPIC -O2" && \
    make -j$(nproc) dep && \
    make -j$(nproc) && \
    make install && \
    ldconfig && \
    cd pjsip-apps/src/swig && \
    make -j$(nproc) python && \
    cd python && \
    python3 setup.py install

# Collect ALL PJSIP-installed shared libraries into one directory for easy copying
RUN mkdir -p /pjsip-libs && \
    cp /usr/lib/libpj*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libpjsua*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libsrtp*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libresample*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libspeex*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libwebrtc*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libg7221*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libgsmcodec*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libilbc*.so* /pjsip-libs/ 2>/dev/null; \
    cp /usr/lib/libyuv*.so* /pjsip-libs/ 2>/dev/null; \
    ls -la /pjsip-libs/ || true

# ============================================
# Stage 2: Runtime
# ============================================
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    espeak-ng \
    sox libsox-fmt-all \
    ffmpeg \
    curl \
    libasound2 \
    alsa-utils \
    libsrtp2-1 \
    && rm -rf /var/lib/apt/lists/*

# Copy ALL PJSIP libs from builder in one shot (srtp, resample, speex, webrtc, etc.)
COPY --from=pjsip-builder /pjsip-libs/ /usr/lib/
COPY --from=pjsip-builder /usr/lib/python3/dist-packages/ /usr/lib/python3/dist-packages/
COPY --from=pjsip-builder /usr/local/lib/python3.10/dist-packages/ /usr/local/lib/python3.10/dist-packages/
RUN ldconfig

WORKDIR /app

# Install Python dependencies (NO piper-tts — we use standalone binary instead)
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# ============================================
# Download standalone Piper TTS binary
# Self-contained: includes own espeak-ng, phonemizer, ONNX runtime
# This avoids the broken piper-tts Python package's phonemizer issues
# ============================================
RUN mkdir -p /tmp/piper-dl && cd /tmp/piper-dl && \
    curl -sSL -o piper.tar.gz \
    https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz && \
    tar -xzf piper.tar.gz && \
    mv piper /app/piper && \
    chmod +x /app/piper/piper && \
    rm -rf /tmp/piper-dl && \
    echo "Piper binary installed at /app/piper/piper" && \
    ls -la /app/piper/

# Download Piper TTS voice models
# Dutch: nathalie (community favorite, clear female voice) + rdh (male alternative)
# English: amy (female) + ryan-high (male, higher quality)
# NOTE: nl_NL-mls is KNOWN to be garbled/unusable — do NOT use it
RUN mkdir -p /app/models/piper && cd /app/models/piper && \
    curl -sSL -o en_US-amy-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx && \
    curl -sSL -o en_US-amy-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json && \
    curl -sSL -o en_US-ryan-high.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx && \
    curl -sSL -o en_US-ryan-high.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json && \
    curl -sSL -o nl_BE-nathalie-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/nathalie/medium/nl_BE-nathalie-medium.onnx && \
    curl -sSL -o nl_BE-nathalie-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/nathalie/medium/nl_BE-nathalie-medium.onnx.json && \
    curl -sSL -o nl_BE-rdh-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/rdh/medium/nl_BE-rdh-medium.onnx && \
    curl -sSL -o nl_BE-rdh-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_BE/rdh/medium/nl_BE-rdh-medium.onnx.json

# Download Silero VAD ONNX model (v4.0 — compatible with our ONNX code's state shapes)
# v5+ changed LSTM state dimensions and is NOT compatible with the v4 API
RUN mkdir -p /app/models && \
    curl -sSL -o /app/models/silero_vad.onnx \
    https://github.com/snakers4/silero-vad/raw/v4.0/files/silero_vad.onnx

# Create directories
RUN mkdir -p /app/logs /app/audio/cache /app/audio/tmp

# Copy application code
COPY src/ /app/src/

# Expose ports
EXPOSE 5061/udp
EXPOSE 4000-4019/udp
EXPOSE 8080/tcp

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python3", "-m", "src.main"]
