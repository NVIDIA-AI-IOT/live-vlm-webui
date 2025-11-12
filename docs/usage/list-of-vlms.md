# Vision-Language Models (VLMs) Reference

A comprehensive list of Vision-Language Models available across different providers, compatible with Live VLM WebUI.

> **Quick Reference:** Use this guide to find the right VLM for your use case and ensure you're selecting a vision-capable model.

## Table of Contents

- [Ollama](#ollama)
- [NVIDIA API Catalog](#nvidia-api-catalog)
- [OpenAI](#openai)
- [Anthropic](#anthropic)
- [Choosing the Right Model](#choosing-the-right-model)

---

## Ollama

Ollama provides the easiest way to run VLMs locally. All models listed below support vision capabilities.

> **Browse all models:** [https://ollama.com/search?c=vision](https://ollama.com/search?c=vision)

### Popular Models

| Model | Sizes Available | Description | Best For |
|-------|----------------|-------------|----------|
| **qwen3-vl** | 2b, 4b, 8b, 30b, 32b, 235b | Most powerful vision-language model in Qwen family | Complex reasoning, tool use, cloud deployment |
| **llama3.2-vision** | 11b, 90b | Meta's first vision-capable Llama model | General-purpose, high accuracy |
| **llama4** | 16x17b, 128x17b | Meta's latest multimodal models | State-of-the-art performance, tool use |
| **qwen2.5vl** | 3b, 7b, 32b, 72b | Flagship Qwen vision model | High performance, multilingual |
| **gemma3** | 4b, 12b, 27b (vision); 270m, 1b (text-only) | Google's capable single-GPU model | Efficiency, single GPU deployment |

### Specialized Models

| Model | Sizes Available | Description | Best For |
|-------|----------------|-------------|----------|
| **granite3.2-vision** | 2b | IBM's document understanding model | Tables, charts, diagrams, documents |
| **moondream** | 1.8b | Tiny vision model for edge devices | Edge devices, low resource environments |
| **llava** | 7b, 13b (vision); 34b (text-only) | Original vision encoder + Vicuna (v1.6) | General-purpose, well-tested |
| **llava-llama3** | 8b | LLaVA fine-tuned on Llama 3 | Better benchmark scores than original |
| **llava-phi3** | 3.8b | Small LLaVA based on Phi 3 Mini | Compact, efficient |
| **bakllava** | 7b | Mistral 7B + LLaVA architecture | Mistral-based alternative |
| **minicpm-v** | 8b | Multimodal LLM series | Compact, efficient |

### Mistral Models

| Model | Sizes Available | Description | Best For |
|-------|----------------|-------------|----------|
| **mistral-small3.2** | 24b | Latest Mistral Small with vision | Function calling, reduced repetition |
| **mistral-small3.1** | 24b | Vision + 128k context | Long context, vision understanding |

### Usage Examples

```bash
# Pull a model
ollama pull llama3.2-vision:11b

# List available models
ollama list

# Run directly
ollama run llama3.2-vision:11b

# Use with Live VLM WebUI
live-vlm-webui --api-base http://localhost:11434/v1 \
               --model llama3.2-vision:11b
```

### Size Recommendations

- **Edge devices (Jetson Orin Nano):** moondream:1.8b, qwen2.5vl:3b, gemma3:4b
- **Single GPU (8-16GB VRAM):** llama3.2-vision:11b, llava:7b, qwen2.5vl:7b, gemma3:4b
- **High-end GPU (24GB+ VRAM):** llama3.2-vision:90b, qwen3-vl:32b, llama4, gemma3:12b/27b
- **Cloud/Multi-GPU:** qwen3-vl:235b, llama4:128x17b

---

## NVIDIA API Catalog

NVIDIA provides enterprise-grade VLMs through NIM (NVIDIA Inference Microservices), available via API or self-hosted containers.

> **Get API Key:** [https://build.nvidia.com](https://build.nvidia.com) (free tier available)

### Available Models

> 🔍 **Note:** List verified from NVIDIA API (https://integrate.api.nvidia.com/v1/models). Check [build.nvidia.com](https://build.nvidia.com) for latest models and detailed specifications.

#### Meta Models
| Model | Parameters | Description | Best For |
|-------|------------|-------------|----------|
| **meta/llama-3.2-90b-vision-instruct** | 90b | Llama 3.2 Vision flagship | High accuracy, production use |
| **meta/llama-3.2-11b-vision-instruct** | 11b | Llama 3.2 Vision compact | Balance of speed and quality |

#### Microsoft Models
| Model | Parameters | Description | Best For |
|-------|------------|-------------|----------|
| **microsoft/phi-4-multimodal-instruct** | ~14b | Latest Phi-4 with multimodal | Latest Microsoft model |
| **microsoft/phi-3.5-vision-instruct** | ~4b | Fast OCR specialist | Text extraction, multi-image |
| **microsoft/phi-3-vision-128k-instruct** | ~4b | Phi-3 with 128k context | Long context vision tasks |

#### NVIDIA Models
| Model | Parameters | Description | Best For |
|-------|------------|-------------|----------|
| **nvidia/vila** | ~40b | General-purpose vision model | Versatile applications |
| **nvidia/neva-22b** | 22b | NVGPT + CLIP | Balanced performance |
| **nvidia/nemotron-nano-12b-v2-vl** | 12b | Compact Nemotron vision | Edge/efficient deployment |
| **nvidia/llama-3.1-nemotron-nano-vl-8b-v1** | 8b | Llama-based vision variant | Compact, efficient |

#### Google Models
| Model | Parameters | Description | Best For |
|-------|------------|-------------|----------|
| **google/gemma-3-27b-it** | 27b | Gemma 3 with vision (128k context) | High quality, large context |
| **google/gemma-3-12b-it** | 12b | Gemma 3 with vision (128k context) | Balanced performance |
| **google/gemma-3-4b-it** | 4b | Gemma 3 with vision (128k context) | Efficient, compact |
| **google/paligemma** | ~3b | Vision-language model | General vision tasks |
| **google/deplot** | - | Chart/plot understanding | Data visualization extraction |

#### Other Models
| Model | Parameters | Description | Best For |
|-------|------------|-------------|----------|
| **microsoft/kosmos-2** | ~1.6b | Multimodal grounding | Object grounding, referring |
| **adept/fuyu-8b** | 8b | Simplified architecture | Fast inference |

### Usage Example

```bash
# Using NVIDIA API Catalog with Live VLM WebUI
live-vlm-webui --api-base https://integrate.api.nvidia.com/v1 \
               --model meta/llama-3.2-11b-vision-instruct \
               --api-key nvapi-xxxxxxxxxxxxx
```

### Features

- ✅ Free tier available (rate-limited)
- ✅ Enterprise SLA options
- ✅ Self-hosted NIM containers available
- ✅ Optimized for NVIDIA GPUs
- ✅ Production-ready APIs
- ✅ **16 vision-capable models** verified from API

### How to List Available Models Yourself

```bash
# Query the API to see all available models
curl -s https://integrate.api.nvidia.com/v1/models | python3 -m json.tool

# Or with an API key to see model details
curl -s https://integrate.api.nvidia.com/v1/models \
  -H "Authorization: Bearer $NVIDIA_API_KEY" | python3 -m json.tool
```

Models with "vision", "vl" (vision-language), or "multimodal" in their names support image input.

---

## OpenAI

OpenAI provides state-of-the-art multimodal models through their API.

> **API Documentation:** [https://platform.openai.com/docs/guides/vision](https://platform.openai.com/docs/guides/vision)

### Available Models

| Model | Description | Best For |
|-------|-------------|----------|
| **gpt-5** | Latest flagship model (released Aug 2025) | State-of-the-art quality, most advanced |
| **gpt-4o** | Previous flagship multimodal model | High quality, complex reasoning |
| **gpt-4o-mini** | Faster, more affordable GPT-4o | Cost-effective, good quality |
| **gpt-4-turbo** | Previous generation with vision | High quality, lower cost |
| **gpt-4-vision-preview** | Earlier vision preview | Legacy applications |

### Usage Example

```bash
# Using OpenAI with Live VLM WebUI
live-vlm-webui --api-base https://api.openai.com/v1 \
               --model gpt-5 \
               --api-key sk-xxxxxxxxxxxxx
```

### Features

- ✅ Industry-leading accuracy
- ✅ Fast response times
- ✅ Excellent instruction following
- ✅ Multimodal (text + images)
- 💰 Pay-per-use pricing

### Pricing Considerations

- **gpt-5:** Highest quality, highest cost
- **gpt-4o:** High quality, premium pricing
- **gpt-4o-mini:** Best balance of quality and cost
- **gpt-4-turbo:** Middle ground option

---

## Anthropic

Anthropic provides the Claude family of models with vision capabilities.

> **API Documentation:** [https://docs.anthropic.com/claude/docs/vision](https://docs.anthropic.com/claude/docs/vision)

### Available Models

| Model | Description | Context | Best For |
|-------|-------------|---------|----------|
| **claude-3.5-sonnet** | Latest and most capable | 200k tokens | Best overall performance |
| **claude-3-opus** | Most powerful Claude 3 | 200k tokens | Complex reasoning tasks |
| **claude-3-sonnet** | Balanced performance | 200k tokens | Speed and quality balance |
| **claude-3-haiku** | Fastest and most compact | 200k tokens | Quick responses, cost-effective |

### Usage Example

```bash
# Using Anthropic with Live VLM WebUI
live-vlm-webui --api-base https://api.anthropic.com/v1 \
               --model claude-3.5-sonnet \
               --api-key sk-ant-xxxxxxxxxxxxx
```

### Features

- ✅ Large 200k token context windows
- ✅ Strong safety and ethics focus
- ✅ Excellent reasoning capabilities
- ✅ All models support vision
- 💰 Competitive pricing

### Model Selection

- **claude-3.5-sonnet:** Best for most use cases (recommended)
- **claude-3-opus:** When you need maximum capability
- **claude-3-haiku:** For high-volume, cost-sensitive applications

---

## Choosing the Right Model

### By Deployment Environment

#### Local/On-Premise (Ollama)
- **Best overall:** `llama3.2-vision:11b` or `qwen2.5vl:7b`
- **Edge devices:** `moondream:1.8b` or `qwen2.5vl:3b`
- **High-end hardware:** `llama3.2-vision:90b` or `qwen3-vl:32b`
- **Document analysis:** `granite3.2-vision:2b`

#### Cloud API
- **Best quality:** OpenAI `gpt-5` or Anthropic `claude-3.5-sonnet`
- **Best value:** OpenAI `gpt-4o-mini` or Anthropic `claude-3-haiku`
- **NVIDIA ecosystem:** NVIDIA API `meta/llama-3.2-11b-vision-instruct`

### By Use Case

#### General Purpose Vision
1. **Cloud:** gpt-5, claude-3.5-sonnet
2. **Local:** llama3.2-vision:11b, qwen2.5vl:7b

#### Document Understanding
1. **Local:** granite3.2-vision:2b
2. **Cloud:** google/deplot (NVIDIA), gpt-5

#### OCR / Text Extraction
1. **Cloud:** microsoft/phi-3.5-vision (NVIDIA)
2. **Local:** qwen2.5vl:7b, llama3.2-vision:11b

#### Edge Deployment
1. moondream:1.8b
2. qwen2.5vl:3b
3. gemma3:4b

#### Cost-Sensitive Production
1. gpt-4o-mini (OpenAI)
2. claude-3-haiku (Anthropic)
3. llama3.2-vision:11b (self-hosted)

### By Hardware

#### 4-8GB VRAM
- moondream:1.8b
- qwen2.5vl:3b
- gemma3:4b (⚠️ Note: gemma3:1b and gemma3:270m are text-only, no vision)

#### 8-16GB VRAM
- llama3.2-vision:11b
- llava:7b
- qwen2.5vl:7b
- bakllava:7b

#### 24GB VRAM
- llama3.2-vision:11b (fast)
- qwen2.5vl:32b
- llava:13b
- mistral-small3.2:24b

#### 40GB+ VRAM or Multi-GPU
- llama3.2-vision:90b
- qwen3-vl:235b
- llama4:16x17b

---

## Model Verification

### How to Verify a Model Supports Vision

#### Ollama
```bash
# Check model details
ollama show llama3.2-vision:11b

# Look for indicators:
# - "vision" in the name
# - "multimodal" in description
# - Vision-related parameters
```

#### Visual Test
1. Point camera at a distinctive object (colored item, text, etc.)
2. Ask: "What color is the object in front of the camera?"
3. If response is generic/unrelated → text-only model (not vision-capable)
4. If response describes the actual object → vision model ✓

### Common Mistakes

#### ❌ Text-Only Models (Will Hallucinate)
These models cannot see images and will generate plausible but incorrect descriptions:

- `llama3.1:8b` ❌ (text-only)
- `phi3.5:3.8b` ❌ (text-only, not vision)
- `qwen2:7b` ❌ (text-only)
- `mistral:7b` ❌ (text-only)
- `gemma3:270m` ❌ (text-only)
- `gemma3:1b` ❌ (text-only)
- `llava:34b` ❌ (text-only, despite smaller llava models having vision)

#### ✅ Vision Models
Always look for "vision" in the model name or verify multimodal capabilities:

- `llama3.2-vision:11b` ✓
- `qwen2.5vl:7b` ✓ (vl = vision-language)
- `llava:7b` ✓
- `llava:13b` ✓
- `moondream:latest` ✓
- `gemma3:4b` ✓ (but not gemma3:1b or 270m)

---

## Troubleshooting

### Model Returns Generic/Hallucinated Responses

**Cause:** You're using a text-only model instead of a vision model.

**Solution:** Switch to a vision-capable model from this list.

See: [Troubleshooting Guide - VLM Hallucination](../troubleshooting.md#vlm-output-is-non-relevant-or-generic-hallucinating)

### Model Not Found

**Cause:** Model not loaded or incorrect name.

**Solutions:**
- **Ollama:** Run `ollama pull <model>` first
- **APIs:** Verify model name exactly matches provider's API
- Check model availability in your region/subscription

### Out of Memory Errors

**Cause:** Model too large for your GPU.

**Solutions:**
- Choose a smaller model variant (e.g., 7b instead of 13b)
- Use quantized models (Q4, Q8 variants in Ollama)
- Switch to cloud API providers

---

## Additional Resources

- **Ollama Models:** [https://ollama.com/search?c=vision](https://ollama.com/search?c=vision)
- **NVIDIA API Catalog:** [https://build.nvidia.com](https://build.nvidia.com)
- **OpenAI Vision Guide:** [https://platform.openai.com/docs/guides/vision](https://platform.openai.com/docs/guides/vision)
- **Anthropic Claude Docs:** [https://docs.anthropic.com/claude/docs/vision](https://docs.anthropic.com/claude/docs/vision)
- **Live VLM WebUI Docs:** [../README.md](../../README.md)

---

## Contributing

Found a new VLM or noticed outdated information? Please contribute:

1. Check if the model is vision-capable
2. Test with Live VLM WebUI if possible
3. Submit a pull request or open an issue

---

**Last Updated:** November 2025

**Note:** Model availability, pricing, and capabilities are subject to change. Always verify with the official provider documentation for the most current information.
