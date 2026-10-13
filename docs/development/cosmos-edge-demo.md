# Cosmos3-Nano INT4 setup from this repository

The [Thor installation guide](../setup/thor-deployment.md) covers the web UI and
segmentation. Cosmos is an optional VLM backend: install its dependencies on the
new Jetson, obtain or rebuild the checkpoint, and build its TensorRT engines on
that Jetson. No archive, copied virtual environment, old benchmark directory or
prebuilt engine is required.

The recipe targets **Jetson Thor T5000, L4T 39.2.1 / JetPack 7.2**, Python 3.12,
CUDA 13.2, TensorRT 10.16.2.10 and TensorRT Edge-LLM 0.11.0. Other GPU/software
combinations need their own validation. The application's OpenAI-compatible
backend remains available for another independently installed VLM server.

## 1. Install the native runtime

From the repository root:

```bash
python3 scripts/setup_cosmos_edge_thor.py runtime --dry-run
python3 scripts/setup_cosmos_edge_thor.py runtime
```

This downloads hash-verified NVIDIA TensorRT/CUDA packages using the shared
`prepare_segmentation_runtime.py` helper, builds the Cosmos tool container from
a pinned ARM64-capable base, installs the pinned official Edge-LLM wheel and
Python requirements in an isolated environment, fetches the pinned NVIDIA source
header, and compiles the small encoder-cache compatibility adapter locally.
A configuration-only check verifies its native ABI without loading a model.
It does not install host drivers or start a model server.

Defaults:

| Item | Location |
| --- | --- |
| Cosmos working directory | `.models/cosmos` |
| TensorRT/CUDA dependency prefix, shared with segmentation | `.models/thor-runtime` |
| Python runtime | `.models/cosmos/edge-venv` |
| Model checkpoint | `.models/cosmos/model` |
| Locally compiled cache adapter | `.models/cosmos/native-compat` |
| Target engine cache | `.models/cosmos/engine-cache` |
| Tool container | `live-vlm-cosmos:thor` |

Use `--work-dir` and `--runtime-prefix` on setup commands to change these paths.
The serving wrapper uses `--models-dir` for that same working directory. Model
and engine locations can be overridden with `--model-dir` and `--cache-dir`.
Paths are resolved on the current machine; there is no assumed home directory
or Jetson IP. The script requires host Docker/NVIDIA Container Toolkit and Git.
The downloaded SDK libraries are extracted into the user-owned prefix.

The container's base is
`vllm/vllm-openai@sha256:aff65d7198dd284c37dd0a18a606544cc5e92bfb0d5eb608b77e8b8f1c6b8b0d`.
Its normal vLLM entrypoint is replaced by the Edge-LLM environment. The official
Edge wheel is `tensorrt_edgellm-0.11.0-cp312-cp312-manylinux_2_39_aarch64.whl`,
SHA256 `9e6722a5a250d0ba18665911a908aecfc9684722b9d56d944efe31fe6c889d9b`.
The requirements pin the critical recorded packages, not every transitive Python
or operating-system package. Native payload and ABI checks reject mismatches.

## 2. Obtain the model

Choose one of the two routes below. Install the current Hugging Face `hf` CLI
on the host first, and use `hf auth login` when repository access requires it.
Credentials are handled by that CLI and are never copied into an image or a
repository configuration file.

### Access to the existing INT4 checkpoint

The original checkpoint is available in
[`adsahu-nv/Cosmos3-Nano-Reasoner-INT4-AWQ`](https://huggingface.co/adsahu-nv/Cosmos3-Nano-Reasoner-INT4-AWQ),
which was **private when checked on October 12, 2026**. A person granted access
can download the pinned revision directly; this is not a public dependency of
the web UI:

```bash
hf auth login
python3 scripts/setup_cosmos_edge_thor.py model-download
```

The default revision is `80a3d4750b9d8b4674cc13342c58bb5c1d308184`. The script
verifies the 7,335,463,640-byte weight file against SHA256
`0f2e1c7756869adcac92652d0d39a0b2196e8ede3d089c837418860f8a0c0b9d`
and inspects the packed checkpoint. It does not change repository visibility or
require anybody to copy a model folder from another Jetson.

### Build INT4 from the public NVIDIA model

This route needs no access to the private INT4 repository. All required helper
source is included under `scripts/cosmos/` with its license and provenance.
Read the upstream model and calibration dataset terms before downloading them.

```bash
python3 scripts/setup_cosmos_edge_thor.py model-build --dry-run
python3 scripts/setup_cosmos_edge_thor.py model-build
```

This is an explicit, substantial GPU conversion job. Allow **at least 100 GB of
free storage** for approximately 31.5 GB of source weights, a 17.5 GB BF16
reasoner, the 7.34 GB INT4 result, environments, calibration images and temporary
files. Run it when the GPU has enough free memory; it is separate from serving.
The script refuses existing conversion output directories rather than overwrite
another checkpoint. Failed conversion artifacts remain available for inspection.

The steps and pins are:

1. Download [`nvidia/Cosmos3-Nano`](https://huggingface.co/nvidia/Cosmos3-Nano/tree/e59a53c25979a090fa8706c9acc0c254a6e89b92)
   at revision `e59a53c25979a090fa8706c9acc0c254a6e89b92`.
2. Extract all 750 reasoner/vision tensors into a Qwen3-VL-compatible BF16
   checkpoint, validating tensor names, shapes and copied bytes. No Qwen weights
   are substituted; the architecture reference is metadata only.
3. Prepare 128 balanced single-image MMMU validation examples across 30 subjects,
   plus 64 separate screening examples, from dataset revision
   `364f2e2eb107b36e07ff4c5a15f5947a759cef47` of
   [`lmms-lab/MMMU`](https://huggingface.co/datasets/lmms-lab/MMMU).
4. Run ModelOpt **0.45.0 AWQ Lite**, group size 128, using seed `20261003` and
   the pinned [Edge-LLM source](https://github.com/NVIDIA/TensorRT-Edge-LLM/tree/95515c2f87fba8982db5a519f9022277667b3cc9).
   Its separate conversion environment uses PyTorch 2.13.0+cu130,
   torchvision 0.28.0+cu130 and Transformers 5.14.1. Only the 252 decoder
   attention/MLP projections become INT4; vision/DeepStack, embeddings, output
   head, activations and KV remain FP16 in deployment.
5. Repair the legacy image-processor bounds to the calibrated 65,536–1,048,576
   pixel range, then inspect packing and scale values.

A fresh GPU calibration is not guaranteed to reproduce identical weight bytes
or accuracy. The 64-example screening comparison is a development check, not
accuracy certification. Review its reports in `quantization-evaluation/`, then
validate the new packed engines and your actual camera workload.

## 3. Check, build engines and serve

```bash
./scripts/run_cosmos_edge_thor.sh --check
./scripts/run_cosmos_edge_thor.sh
```

The check starts short-lived containers for runtime version, native configuration
ABI and model metadata checks. It does not construct a model or run inference.
`--dry-run` on either setup or serving prints commands without contacting Docker,
downloading files or writing directories.

The first serving run builds both language and visual engines on this target
through the official direct builder. Wait for startup to finish; subsequent
runs reuse matching local engines. New model content or profile settings require
new engines. No opaque compiled adapter or saved TensorRT engine is distributed.

The endpoint defaults to `http://127.0.0.1:8012/v1`, model `cosmos-nano-int4`,
container name `live-vlm-cosmos-edge`. Use `--host`, `--port`, `--name` or `--image`
for explicit overrides. An existing container is never stopped automatically.
The engine profile is batch 4 with in-flight batching, input 2,048 tokens,
KV capacity 16,384 tokens and 64–1,024 visual tokens. The builder's 8 GiB workspace
limit is not a cap on total model/runtime memory.

Check the API, then connect the web UI:

```bash
curl --fail http://127.0.0.1:8012/v1/models
./scripts/run_segmentation_thor.sh --skip-build \
  --host 0.0.0.0 --api-base http://127.0.0.1:8012/v1 \
  --model cosmos-nano-int4 --vlm-backend edgellm
```

Open `https://<this-jetson-ip>:8090/multi-camera`. Start with one active camera,
then test two or four independent outputs. Test the temporal video option
separately. Browser camera sources refer to USB cameras attached to the machine
running the browser; use the browser on the Jetson for its USB webcams.

## One serving instance per model

Cosmos and YOLOE each run once for all camera streams. The application allows up
to four pending Cosmos requests with the `edgellm` backend, retaining separate
prompts, results and timing per camera. Cosmos sees original pixels from the
same source frames used for segmentation, without mask overlays. Temporal mode
supplies a bounded recent clip; single-frame mode remains available. Description
frequency, source FPS and completed segmentation FPS are different measurements.
No one-second four-camera latency or 25-FPS four-camera inference guarantee is
implied by this setup recipe.

The cache adapter sets the Edge-LLM 0.11 encoder embedding budget to zero before
runtime construction to avoid the reproduced DeepStack cache-restore failure.
It checks the public struct, native payload hash and pybind ABI. Requests also
set `reuse_context: false` and `cache_generated_tokens: false` to keep cameras
independent. The stop-string helper is scoped to nonstreaming completions.

The original extraction, quantization and serving pipeline was tested on this
Thor. On October 12, 2026, the repository runtime installation was also run in a
new local environment: public SDK packages and the official Edge wheel were
installed, the adapter was compiled from source, and native ABI plus serving
imports passed. Model metadata checks used the existing checkpoint read-only.
The public source-conversion command was checked without running another full
GPU calibration or engine build. A second Jetson still needs its own engine
build and inference smoke test. Keep the model's OpenMDW notices and the
helper-specific licenses in `scripts/cosmos/` when distributing this source.

## Performance measurements and timing scope

The installation recipe does not promise a fixed FPS or latency. The UI reports
completed segmentation FPS separately from camera FPS and the rate of VLM
responses. VLM end-to-end latency measures from receipt of the selected source
frame on the server to completion of its response. It excludes camera-to-server
network delay and browser display delay. In temporal mode, the preceding context
window is additional history, not an extra interval included in that latency.

See [real-time viewing](realtime-demo.md) and [temporal video](temporal-video.md)
for behavior, existing measurement conditions and limitations. Measure again
on the destination, with the actual sources, prompt, output length and number of
streams; concurrency shares one GPU and does not guarantee throughput scaling.
