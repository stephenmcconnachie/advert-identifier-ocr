#!/bin/bash

# Script to start video servers and vLLM service in screen sessions
# Designed to be run from crontab
# Usage: ./start_screen_sessions.sh [model_name]
#   model_name: Optional HuggingFace model ID (default: Qwen/Qwen3.5-4B)

VLLM_MODEL="${1:-Qwen/Qwen3.5-4B}"

# Function to check if a screen session exists
screen_exists() {
    screen -ls | grep -q "\.$1[[:space:]]"
}

# Session 1: Stephen HTTP Video Server (Port 1100)
if ! screen_exists "stephen-http-video-server"; then
    echo "Creating stephen-http-video-server session..."
    screen -dmS stephen-http-video-server bash -c '
        cd /mnt/qnap_04/Admin/code/ai_machine_learning/advert-identifier/video
        python3 -m http.server 1100
    '
    echo "Stephen HTTP video server started on port 1100"
else
    echo "stephen-http-video-server session already exists"
fi

# Session 2: Shihan HTTP Video Server (Port 1000)
if ! screen_exists "shihan-http-video-server"; then
    echo "Creating shihan-http-video-server session..."
    screen -dmS shihan-http-video-server bash -c '
        cd /home/shihan/storage/video
        python3 -m http.server 2000
    '
    echo "Shihan HTTP video server started on port 2000"
else
    echo "shihan-http-video-server session already exists"
fi

# Session 3: Multimodal vLLM Server
if ! screen_exists "multimodal-vLLM"; then
    echo "Creating multimodal-vLLM session for ${VLLM_MODEL}..."
    screen -dmS multimodal-vLLM bash -c "
        cd /home/datadigipres/code/vllm
        source bin/activate
        export HF_HOME=/mnt/qnap_04/Admin/code/ai_machine_learning/models
        export HF_HUB_CACHE=/mnt/qnap_04/Admin/code/ai_machine_learning/models/hub
        CUDA_VISIBLE_DEVICES=\"0\" vllm serve \"${VLLM_MODEL}\" \
            --reasoning-parser qwen3 \
            --max-model-len 65536 \
            --max-num-seqs 16 \
            --gpu-memory-utilization 0.9 \
            --media-io-kwargs \"{\\\"video\\\": {\\\"num_frames\\\": -1}}\" \
            --limit-mm-per-prompt \"{\\\"video\\\": {\\\"count\\\": 1, \\\"num_frames\\\": 1048, \\\"width\\\": 452, \\\"height\\\": 256}}\" \
            --allowed-local-media-path /tmp \
            --enable-prefix-caching \
            2>&1 | tee /mnt/qnap_04/Admin/code/ai_machine_learning/logs/vllm_serve.log
    "
    echo "Multimodal vLLM server started with ${VLLM_MODEL}"
else
    echo "multimodal-vLLM session already exists"
fi

echo ""
echo "Active screen sessions:"
screen -ls
