# Advanced Configuration

This guide covers advanced configuration options for power users.

## Command-Line Options

```bash
python server.py --help
```

**Required:**
- `--model MODEL` - VLM model name (e.g., `llama-3.2-11b-vision-instruct`)

**Optional:**
- `--host HOST` - Host to bind to (default: `0.0.0.0`)
- `--port PORT` - Port to bind to (default: `8090`)
- `--api-base URL` - VLM API base URL (default: `http://localhost:8000/v1`)
- `--api-key KEY` - API key, use `EMPTY` for local servers (default: `EMPTY`)
- `--prompt TEXT` - Custom prompt for VLM (default: scene description)
- `--process-every N` - Process every Nth frame (default: `30`)

## Example Configurations

### High-Frequency Updates

More responsive, higher CPU usage:

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --process-every 15
```

### Custom Port and Host

```bash
python server.py \
  --model llava:7b \
  --api-base http://localhost:11434/v1 \
  --host 0.0.0.0 \
  --port 3000
```

### Using OpenAI API

```bash
python server.py \
  --model gpt-4-vision-preview \
  --api-base https://api.openai.com/v1 \
  --api-key your-api-key-here
```

## Performance Tuning

### Frame Processing Rate

Adjust frame processing in two ways:

**Via Command Line** (at startup):
```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --process-every 60  # Process every 60 frames
```

**Via Web UI** (while running):
- Go to "Processing Settings" in the left sidebar
- Change "Frame Processing Interval" (1-3600 frames)
- Click "Apply Settings" - takes effect immediately!

**Guidelines:**
- **Lower values** (5-15 frames) = more frequent analysis, higher GPU usage (~2-6 FPS @ 30fps)
- **Default** (30 frames) = balanced, ~1 FPS analysis @ 30fps video
- **Higher values** (60-120 frames) = less frequent, good for monitoring (~0.25-0.5 FPS)
- **Very high** (300-3600 frames) = infrequent updates for benchmarking
  - 300 frames = ~10 second intervals @ 30fps
  - 900 frames = ~30 second intervals @ 30fps
  - 3600 frames = ~2 minute intervals @ 30fps

### Model Selection

Choose based on your hardware and needs:

**Fast models (good for prototyping):**
- `llava:7b` (Ollama)
- `llava-1.5-7b-hf` (vLLM/SGLang)

**Balanced:**
- `llama-3.2-11b-vision-instruct` (recommended)
- `llava:13b`

**High quality** (requires significant GPU memory):
- `llama-3.2-90b-vision-instruct` (Ollama/NVIDIA)
- `qwen2.5vl:32b` (Ollama)
- `gpt-4-vision-preview` (via OpenAI API)

> ⚠️ **Note:** `llava:34b` is text-only and does not support vision despite smaller llava models having vision capabilities.

### Video Resolution

Edit `index.html` to change the requested video resolution:

```javascript
video: {
    width: { ideal: 640 },   // Lower for better performance
    height: { ideal: 480 }
}
```

## Custom Prompts - Beyond Captioning

The real power is in custom prompts! Here are examples:

### Scene Description (default)

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe what you see in this image in one sentence."
```

### Object Detection

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "List all objects you can see in this image."
```

### Safety Monitoring

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Alert me if you see any safety hazards or dangerous situations."
```

### Activity Recognition

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "What activity is the person performing?"
```

### Accessibility

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe the scene in detail for a visually impaired person."
```

### Emotion/Expression Detection

```bash
python server.py \
  --model llama-3.2-11b-vision-instruct \
  --api-base http://localhost:8000/v1 \
  --ssl-cert cert.pem --ssl-key key.pem \
  --prompt "Describe the facial expressions and emotions you observe."
```

## API Compatibility

This tool uses the OpenAI chat completions API format with vision support. Any backend that implements this standard will work.

### Tested Backends
- ✅ **vLLM** - Best performance, production-ready
- ✅ **SGLang** - Great for complex prompts
- ✅ **Ollama** - Easiest setup
- ✅ **OpenAI API** - Cloud-based (requires API key)

### Message Format

```python
{
  "model": "model-name",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "your prompt"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }]
}
```

## Development

### Customizing the VLM Service

Edit `vlm_service.py` to customize API calls:

```python
# Add custom parameters
response = await self.client.chat.completions.create(
    model=self.model,
    messages=messages,
    max_tokens=self.max_tokens,
    temperature=0.7,  # Adjust for creativity
    top_p=0.9,        # Adjust for diversity
)
```

### WebSocket Communication

The server uses WebSocket for real-time bidirectional communication:

**Server → Client:**
- `vlm_response` - VLM analysis results and metrics
- `gpu_stats` - System monitoring data (GPU, CPU, RAM)
- `status` - Connection and processing status updates

**Client → Server:**
- `update_prompt` - Change prompt and max_tokens on-the-fly
- `update_model` - Switch VLM model without restart
- `update_processing` - Adjust frame processing interval

Example: Sending a prompt update from JavaScript:

```javascript
websocket.send(JSON.stringify({
    type: 'update_prompt',
    prompt: 'Describe the scene',
    max_tokens: 100
}));
```

### Adding New GPU Monitors

Extend `gpu_monitor.py` for new platforms:

```python
class AppleSiliconMonitor(GPUMonitor):
    """Monitoring for Apple M1/M2/M3 chips"""

    def get_stats(self) -> Dict:
        # Use powermetrics or ioreg to get GPU stats
        # Return standardized dict format
        pass
```

### Customizing the UI Theme

Edit CSS variables in `index.html` to customize colors:

```css
:root {
    --nvidia-green: #76B900;  /* NVIDIA brand color */
    --bg-primary: #000000;    /* Dark theme background */
    --text-primary: #FFFFFF;  /* Text color */
    /* ... more variables */
}
```
