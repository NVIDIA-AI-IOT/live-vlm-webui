# SPDX-License-Identifier: Apache-2.0
"""Optional, serialized CUDA segmentation with masks-only video compositing."""

import asyncio
import importlib
import json
import math
import os
import struct
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import av
import cv2
import numpy as np

DEFAULT_CLASSES = ("person", "car", "truck", "bus", "bicycle", "motorcycle")
RETAIL_CLASSES = ("person", "shopping cart", "shopping basket", "backpack")
RETAIL_ENGINE_SHAPE = (288, 480)
RETAIL_IMGSZ = 480
RETAIL_RECOMMENDED_CONFIDENCE = 0.05


class SegmentationUnavailable(RuntimeError):
    """The optional segmentation runtime or its local assets are unavailable."""


@dataclass(frozen=True)
class SegmentationConfig:
    enabled: bool = False
    confidence: float = 0.25
    opacity: float = 0.45
    classes: tuple[str, ...] = DEFAULT_CLASSES
    imgsz: int = 640
    max_det: int = 50
    target_fps: int = 25

    @classmethod
    def from_dict(cls, value):
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("segmentation must be a JSON object")  # noqa: TRY004 - API validation
        if set(value) - {
            "enabled",
            "confidence",
            "opacity",
            "classes",
            "imgsz",
            "max_det",
            "target_fps",
        }:
            raise ValueError("Unknown segmentation setting")
        settings = dict(value)
        if "enabled" in settings and not isinstance(settings["enabled"], bool):
            raise ValueError("segmentation.enabled must be a boolean")
        for key in ("confidence", "opacity"):
            if key in settings:
                number = settings[key]
                if (
                    isinstance(number, bool)
                    or not isinstance(number, (int, float))
                    or not math.isfinite(number)
                    or not 0 <= number <= 1
                ):
                    raise ValueError(f"segmentation.{key} must be a finite number between 0 and 1")
        for key, minimum, maximum in (
            ("imgsz", 320, 1280),
            ("max_det", 1, 300),
            ("target_fps", 1, 60),
        ):
            if key in settings:
                number = settings[key]
                if (
                    isinstance(number, bool)
                    or not isinstance(number, int)
                    or not minimum <= number <= maximum
                ):
                    raise ValueError(
                        f"segmentation.{key} must be an integer from {minimum} to {maximum}"
                    )
        if "imgsz" in settings and settings["imgsz"] % 32:
            raise ValueError("segmentation.imgsz must be a multiple of 32")
        if "classes" in settings:
            names = settings["classes"]
            if (
                not isinstance(names, list)
                or not 1 <= len(names) <= 80
                or any(
                    not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 for name in names
                )
            ):
                raise ValueError("segmentation.classes must contain 1-80 nonempty class names")
            names = tuple(name.strip() for name in names)
            if len(set(names)) != len(names):
                raise ValueError("segmentation.classes must not contain duplicates")
            settings["classes"] = names
        return cls(**settings)

    def to_dict(self):
        result = asdict(self)
        result["classes"] = list(self.classes)
        return result


def composite_masks(image, mask, opacity):
    """Tint masked BGR pixels inside their bounding region, preserving exact rounding."""
    if mask.shape != image.shape[:2]:
        raise SegmentationUnavailable("Segmentation masks do not match the original frame size")
    output = image.copy()
    if not opacity:
        return output
    bitmap = np.asarray(mask, dtype=bool).astype(np.uint8)
    x, y, width, height = cv2.boundingRect(bitmap)
    if not width or not height:
        return output
    color = np.array([0, 255, 0], dtype=np.float32)
    values = np.arange(256, dtype=np.float32)[:, None]
    # Keep the original float32/LUT rounding, but avoid tinting unused image areas.
    lut = np.clip(np.rint(values * (1 - opacity) + color * opacity), 0, 255).astype(np.uint8)
    region = np.s_[y : y + height, x : x + width]
    tinted = cv2.LUT(image[region], lut.reshape(256, 1, 3))
    cv2.copyTo(tinted, bitmap[region], output[region])
    return output


@dataclass
class _ClassProfile:
    embeddings: object
    device_embeddings: object = None


class _UltralyticsBackend:
    """All methods are called exclusively by the service's single worker."""

    def __init__(self, model_path):
        self.model_path = model_path
        self._model = None
        self._profiles = OrderedDict()
        self._active_classes = None
        self._warmed = False
        self._torch = None
        self._yoloe = None

    def status(self):
        if not self.model_path.is_file():
            raise SegmentationUnavailable(
                f"YOLOE model is missing at {self.model_path}. Run the segmentation runtime setup."
            )
        encoder = self.model_path.parent / "mobileclip2_b.ts"
        if not encoder.is_file():
            raise SegmentationUnavailable(
                f"YOLOE text encoder is missing at {encoder}. Run the segmentation runtime setup."
            )
        if self._torch is None:
            # Ultralytics otherwise attempts to install CLIP from inside set_classes.
            os.environ["YOLO_AUTOINSTALL"] = "False"
            try:
                torch = importlib.import_module("torch")
                if not torch.cuda.is_available():
                    raise SegmentationUnavailable(
                        "YOLOE segmentation requires an available CUDA GPU"
                    )
                importlib.import_module("clip")
                ultralytics = importlib.import_module("ultralytics")
                self._yoloe = ultralytics.YOLOE
                # An absolute asset directory avoids changing the process working
                # directory or downloading a text encoder during first inference.
                ultralytics.settings.update({"weights_dir": str(self.model_path.parent)})
                self._torch = torch
            except SegmentationUnavailable:
                raise
            except Exception as exc:
                raise SegmentationUnavailable(
                    "CUDA segmentation dependencies are unavailable. Run the segmentation runtime "
                    f"setup. ({type(exc).__name__}: {exc})"
                ) from exc

    def _set_predictor_profile(self, classes, profile):
        predictor = self._model.predictor
        if predictor is None:
            return
        backend = predictor.model
        if backend.format != "pt":
            raise SegmentationUnavailable("Shared YOLOE classes require the native PyTorch backend")
        native_model = backend.model
        if profile.device_embeddings is None:
            parameter = next(native_model.parameters())
            profile.device_embeddings = profile.embeddings.to(
                device=parameter.device, dtype=parameter.dtype
            )
        native_model.set_classes(list(classes), profile.device_embeddings)
        # AutoBackend delegates names reads to its backend. Updating that owner
        # keeps Results metadata consistent with the prompt embeddings and head.
        backend.backend.names = native_model.names

    def prepare(self, config):
        self.status()
        if self._model is None:
            model = self._yoloe(str(self.model_path))
            if model.task != "segment":
                raise SegmentationUnavailable(
                    "The configured YOLOE model must support segmentation"
                )
            self._model = model
        key = config.classes
        if key not in self._profiles:
            # Cache only small text embeddings, never another YOLO checkpoint or
            # CUDA predictor. Text encoding happens once per retained class list.
            embeddings = self._model.get_text_pe(list(key))
            if len(self._profiles) >= 4:
                self._profiles.popitem(last=False)
            self._profiles[key] = _ClassProfile(embeddings)
        self._profiles.move_to_end(key)
        profile = self._profiles[key]
        if self._active_classes != key:
            # A partial switch must not leave a supposedly active profile. The
            # next request reapplies both model views before any inference.
            self._active_classes = None
            self._model.model.set_classes(list(key), profile.embeddings)
            # YOLOE.set_classes resets its predictor. Use the inner model APIs
            # to preserve the warmed CUDA model, weights and predictor identity.
            self._set_predictor_profile(key, profile)
            self._active_classes = key
        if not self._warmed:
            try:
                self._predict(
                    self._model, np.zeros((config.imgsz, config.imgsz, 3), dtype=np.uint8), config
                )
                # Initial prediction creates the sole CUDA predictor. Bind a
                # resident FP16 embedding instead of copying CPU prompts per frame.
                self._set_predictor_profile(key, profile)
            except Exception:
                self._active_classes = None
                predictor = self._model.predictor
                if predictor is not None and getattr(predictor, "model", None) is None:
                    # Ultralytics assigns the predictor before setup_model. A
                    # failed setup has no backend to reuse; retry only that setup
                    # while keeping the already loaded checkpoint and embeddings.
                    self._model.predictor = None
                raise
            self._warmed = True
        return self._model

    @staticmethod
    def _predict_many(model, images, config):
        results = model.predict(
            source=images,
            device="cuda:0",
            quantize=16,
            retina_masks=True,
            conf=config.confidence,
            imgsz=config.imgsz,
            max_det=config.max_det,
            stream=False,
            verbose=False,
            save=False,
            show=False,
        )
        return _UltralyticsBackend._mask_results(results, images)

    @staticmethod
    def _mask_tensor(result, image):
        if result.masks is None:
            if result.boxes is not None and len(result.boxes):
                raise SegmentationUnavailable(
                    "YOLOE returned detections without segmentation masks"
                )
            return None, 0
        masks = result.masks.data
        if masks.ndim != 3 or tuple(masks.shape[1:]) != image.shape[:2]:
            raise SegmentationUnavailable(
                "YOLOE did not return original-resolution segmentation masks"
            )
        return masks, len(masks)

    @staticmethod
    def _mask_result(result, image):
        masks, count = _UltralyticsBackend._mask_tensor(result, image)
        if not count:
            return np.zeros(image.shape[:2], dtype=bool), 0
        return masks.amax(dim=0).bool().cpu().numpy(), count

    @staticmethod
    def _mask_results(results, images):
        if len(results) != len(images):
            raise SegmentationUnavailable("YOLOE returned an unexpected number of frame results")
        if len(images) <= 1 or len({image.shape[:2] for image in images}) != 1:
            return [
                _UltralyticsBackend._mask_result(result, image)
                for result, image in zip(results, images)
            ]
        pairs = [
            _UltralyticsBackend._mask_tensor(result, image)
            for result, image in zip(results, images)
        ]
        shape = images[0].shape[:2]
        if not any(count for _, count in pairs):
            return [(np.zeros(shape, dtype=bool), 0) for _ in pairs]
        # The service groups equal-size images. Union on device, then copy the
        # entire batch once instead of synchronizing a CPU transfer per camera.
        unions = [masks.amax(dim=0).bool() if count else None for masks, count in pairs]
        prototype = next(mask for mask in unions if mask is not None)
        unions = [mask if mask is not None else prototype.new_zeros(shape) for mask in unions]
        torch = importlib.import_module("torch")
        host_masks = torch.stack(unions).cpu().numpy()
        return [(mask, count) for mask, (_, count) in zip(host_masks, pairs)]

    @staticmethod
    def _predict(model, image, config):
        return _UltralyticsBackend._predict_many(model, [image], config)[0]

    def predict(self, image, config):
        return self._predict(self.prepare(config), image, config)

    def predict_many(self, images, config):
        return self._predict_many(self.prepare(config), images, config)

    def close(self):
        self._model = None
        self._profiles.clear()
        self._active_classes = None
        self._warmed = False


def _cpu_postprocess_predictor(torch, base_predictor):
    """Build the engine-only predictor lazily, keeping optional imports out of startup."""

    def to_cpu(value):
        if isinstance(value, torch.Tensor):
            return value.detach().cpu()
        if isinstance(value, list):
            return [to_cpu(item) for item in value]
        if isinstance(value, tuple):
            return tuple(to_cpu(item) for item in value)
        if isinstance(value, dict):
            return {key: to_cpu(item) for key, item in value.items()}
        return value

    class CPUPostprocessPredictor(base_predictor):
        def postprocess(self, preds, img, orig_imgs):
            # Keep network inference and its image tensor on CUDA. Only raw
            # predictions/prototypes move to CPU; Ultralytics owns NMS, original-
            # resolution mask construction, class names and result metadata.
            return super().postprocess(to_cpu(preds), img, orig_imgs)

    return CPUPostprocessPredictor


class _TensorRTRetailBackend:
    """One prebuilt, fixed-label engine; all access stays on the existing worker."""

    def __init__(self, model_path):
        self.model_path = model_path
        self._model = None
        self._torch = None
        self._yolo = None
        self._predictor_type = None
        self._metadata = None
        self._warmed = False

    def _read_metadata(self):
        if not self.model_path.is_file():
            raise SegmentationUnavailable(
                f"TensorRT retail engine is missing at {self.model_path}. "
                "Build the retail segmentation engine before starting this profile."
            )
        try:
            with self.model_path.open("rb") as stream:
                header = stream.read(4)
                if len(header) != 4:
                    raise ValueError("missing metadata header")
                size = struct.unpack("<I", header)[0]
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("invalid metadata length")
                metadata = json.loads(stream.read(size))
                if not stream.read(1):
                    raise ValueError("missing engine payload")
            if not isinstance(metadata, dict):
                raise TypeError("metadata must be an object")
            if metadata.get("task") != "segment":
                raise ValueError("task must be segment")
            if metadata.get("names") != {
                str(index): name for index, name in enumerate(RETAIL_CLASSES)
            }:
                raise ValueError("class names or their order do not match the retail profile")
            if metadata.get("imgsz") != list(RETAIL_ENGINE_SHAPE):
                raise ValueError("input dimensions must be 288x480")
            if metadata.get("batch") != 4:
                raise ValueError("batch capacity must be 4")
            return metadata
        except (OSError, ValueError, TypeError) as exc:
            raise SegmentationUnavailable(
                f"Invalid TensorRT retail engine metadata: {exc}"
            ) from exc

    def status(self):
        if self._metadata is None:
            self._metadata = self._read_metadata()
        if self._torch is None:
            os.environ["YOLO_AUTOINSTALL"] = "False"
            try:
                torch = importlib.import_module("torch")
                if not torch.cuda.is_available():
                    raise SegmentationUnavailable(
                        "TensorRT segmentation requires an available CUDA GPU"
                    )
                self._trt = importlib.import_module("tensorrt")
                ultralytics = importlib.import_module("ultralytics")
                predictor_module = importlib.import_module(
                    "ultralytics.models.yolo.segment.predict"
                )
                self._predictor_type = _cpu_postprocess_predictor(
                    torch, predictor_module.SegmentationPredictor
                )
                self._yolo = ultralytics.YOLO
                self._torch = torch
            except SegmentationUnavailable:
                raise
            except Exception as exc:
                raise SegmentationUnavailable(
                    "TensorRT segmentation dependencies are unavailable. Start the runtime "
                    f"with the validated TensorRT/CUDA libraries. ({type(exc).__name__}: {exc})"
                ) from exc

    def _validate_engine_profile(self):
        backend = self._model.predictor.model
        if backend.format != "engine":
            raise SegmentationUnavailable("The retail profile did not load a TensorRT engine")
        engine = backend.model
        inputs = [
            engine.get_tensor_name(index)
            for index in range(engine.num_io_tensors)
            if engine.get_tensor_mode(engine.get_tensor_name(index)) == self._trt.TensorIOMode.INPUT
        ]
        if inputs != ["images"] or engine.num_optimization_profiles != 1:
            raise SegmentationUnavailable(
                "The retail engine must have one images input and one profile"
            )
        shapes = tuple(tuple(shape) for shape in engine.get_tensor_profile_shape("images", 0))
        expected = tuple((batch, 3, *RETAIL_ENGINE_SHAPE) for batch in (1, 4, 4))
        if shapes != expected:
            raise SegmentationUnavailable(
                "The retail engine requires fixed 288x480 spatial dimensions and batch min/opt/max 1/4/4"
            )

    @staticmethod
    def _validate_config(config):
        if config.classes != RETAIL_CLASSES or config.imgsz != RETAIL_IMGSZ:
            raise SegmentationUnavailable(
                "This TensorRT retail profile requires classes person, shopping cart, "
                "shopping basket, backpack (in that order) and imgsz=480. "
                "Use the PyTorch model for custom classes or other image sizes."
            )

    def _predict_many(self, model, images, config):
        if not 1 <= len(images) <= 4:
            raise SegmentationUnavailable(
                "The TensorRT retail profile accepts batches of 1-4 frames"
            )
        results = model.predict(
            source=images,
            device="cuda:0",
            quantize=16,
            retina_masks=True,
            conf=config.confidence,
            predictor=self._predictor_type,
            imgsz=list(RETAIL_ENGINE_SHAPE),
            # Batch is dynamic, spatial dimensions are fixed. Minimum-rectangle
            # padding can otherwise leave portrait/square inputs outside the profile.
            rect=False,
            # YOLOE.predict defaults to class-agnostic NMS; the generic YOLO
            # engine wrapper must use the same suppression across retail labels.
            agnostic_nms=True,
            max_det=config.max_det,
            stream=False,
            verbose=False,
            save=False,
            show=False,
        )
        return _UltralyticsBackend._mask_results(results, images)

    def prepare(self, config):
        self._validate_config(config)
        self.status()
        if self._model is None:
            self._model = self._yolo(str(self.model_path), task="segment")
        if not self._warmed:
            image = np.zeros((*RETAIL_ENGINE_SHAPE, 3), dtype=np.uint8)
            try:
                for batch_size in range(1, 5):
                    self._predict_many(self._model, [image] * batch_size, config)
                    if batch_size == 1:
                        self._validate_engine_profile()
            except Exception:
                predictor = self._model.predictor
                if predictor is not None and getattr(predictor, "model", None) is None:
                    self._model.predictor = None
                raise
            self._warmed = True
        return self._model

    def predict_many(self, images, config):
        return self._predict_many(self.prepare(config), images, config)

    def predict(self, image, config):
        return self.predict_many([image], config)[0]

    def close(self):
        self._model = None
        self._warmed = False


@dataclass
class _RenderRequest:
    frame: av.VideoFrame
    config: SegmentationConfig
    future: asyncio.Future
    queued_at: float
    started: bool = False

    @property
    def batch_key(self):
        # Same geometry retains Ultralytics' rectangular preprocessing. Opacity
        # and camera pacing are independent of the shared model prediction.
        return (
            self.config.classes,
            self.config.confidence,
            self.config.imgsz,
            self.config.max_det,
            self.frame.width,
            self.frame.height,
        )


class SegmentationService:
    """Bounded batches on one CUDA worker; cancellation owns in-flight GPU work.

    At most sixteen render calls enter the service at once, matching four
    four-camera sessions. Each GPU batch contains up to four compatible frames,
    collected for at most four milliseconds. Camera owners apply target_fps
    pacing before submitting their latest frame; this service never duplicates
    frames to manufacture a requested rate.
    """

    def __init__(self, model_path=None, *, backend_factory=None):
        self.model_path = (
            Path(
                model_path
                or os.environ.get("LIVE_VLM_SEGMENTATION_MODEL", ".models/yoloe-26s-seg.pt")
            )
            .expanduser()
            .resolve()
        )
        self._is_engine = self.model_path.suffix.lower() == ".engine"
        self._backend_factory = backend_factory or (
            _TensorRTRetailBackend if self._is_engine else _UltralyticsBackend
        )
        self._backend = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yoloe-segmentation")
        self._closing = False
        self._close_task = None
        self._error = None
        self._pending = deque()
        self._render_slots = asyncio.Semaphore(16)
        self._batch_task = None

    def _get_backend(self):
        if self._backend is None:
            self._backend = self._backend_factory(self.model_path)
        return self._backend

    @staticmethod
    async def _await_worker(future):
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Canceling an asyncio future cannot stop its CUDA/thread operations.
            # Observe completion before releasing camera ownership or the worker.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001 - preserve the caller's cancellation
                    break
            if not future.cancelled():
                future.exception()
            raise

    async def _run(self, function, *args):
        if self._closing:
            raise SegmentationUnavailable("Segmentation service is closed")
        job = asyncio.get_running_loop().run_in_executor(self._executor, function, *args)
        try:
            return await self._await_worker(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            if isinstance(exc, SegmentationUnavailable):
                raise
            raise SegmentationUnavailable(f"YOLOE segmentation failed: {exc}") from exc

    async def status(self):
        available = False
        error = None
        try:
            await self._run(lambda: self._get_backend().status())
            available = True
        except SegmentationUnavailable as exc:
            error = str(exc)
        return {
            "available": available,
            "model": self.model_path.name,
            "device": "cuda:0",
            "error": error,
            "backend": "tensorrt" if self._is_engine else "pytorch",
            "fixed_classes": list(RETAIL_CLASSES) if self._is_engine else None,
            "fixed_imgsz": RETAIL_IMGSZ if self._is_engine else None,
            "recommended_confidence": (
                RETAIL_RECOMMENDED_CONFIDENCE
                if self._is_engine
                else SegmentationConfig().confidence
            ),
        }

    async def prepare(self, config):
        if config.enabled:
            await self._run(lambda: self._get_backend().prepare(config))

    def _render_many(self, requests):
        started = time.perf_counter()
        images = [request.frame.to_ndarray(format="bgr24") for request in requests]
        backend = self._get_backend()
        inference_started = time.perf_counter()
        if hasattr(backend, "predict_many"):
            predictions = backend.predict_many(images, requests[0].config)
        else:
            predictions = [
                backend.predict(image, request.config) for request, image in zip(requests, images)
            ]
        inference_ms = (time.perf_counter() - inference_started) * 1000
        if len(predictions) != len(requests):
            raise SegmentationUnavailable("YOLOE returned an unexpected number of frame results")
        outputs = []
        for request, image, (mask, count) in zip(requests, images, predictions):
            output = av.VideoFrame.from_ndarray(
                composite_masks(image, mask, request.config.opacity), format="bgr24"
            )
            output.pts = request.frame.pts
            if request.frame.time_base is not None:
                output.time_base = request.frame.time_base
            outputs.append((output, {"inference_ms": inference_ms, "mask_count": int(count)}))
        service_ms = (time.perf_counter() - started) * 1000
        for request, (_, metrics) in zip(requests, outputs):
            metrics.update(
                batch_size=len(requests),
                queue_ms=(started - request.queued_at) * 1000,
                service_ms=service_ms,
            )
        return outputs

    async def _drain_batches(self):
        try:
            while self._pending:
                # Let simultaneously arriving cameras share one model forward
                # pass, without an unbounded wait for a missing/slow camera.
                if len(self._pending) < 4:
                    await asyncio.sleep(0.004)
                if not self._pending:
                    break
                first = self._pending.popleft()
                batch = [first]
                remaining = deque()
                while self._pending:
                    request = self._pending.popleft()
                    if len(batch) < 4 and request.batch_key == first.batch_key:
                        batch.append(request)
                    else:
                        remaining.append(request)
                self._pending = remaining
                for request in batch:
                    request.started = True
                try:
                    # Already accepted frames must finish even when close starts.
                    job = asyncio.get_running_loop().run_in_executor(
                        self._executor, self._render_many, batch
                    )
                    outputs = await self._await_worker(job)
                except Exception as exc:  # noqa: BLE001 - deliver GPU errors to all batch owners
                    self._error = str(exc)
                    for request in batch:
                        request.future.set_exception(
                            SegmentationUnavailable(f"YOLOE segmentation failed: {exc}")
                        )
                else:
                    completed_at = time.perf_counter()
                    for request, (output, metrics) in zip(batch, outputs):
                        metrics["total_ms"] = (completed_at - request.queued_at) * 1000
                        request.future.set_result((output, metrics))
        finally:
            self._batch_task = None

    async def render(self, frame, config):
        if not config.enabled:
            return frame, {"inference_ms": 0.0, "mask_count": 0}
        if self._closing:
            raise SegmentationUnavailable("Segmentation service is closed")
        async with self._render_slots:
            if self._closing:
                raise SegmentationUnavailable("Segmentation service is closed")
            request = _RenderRequest(
                frame, config, asyncio.get_running_loop().create_future(), time.perf_counter()
            )
            self._pending.append(request)
            if self._batch_task is None:
                self._batch_task = asyncio.create_task(self._drain_batches())
            try:
                return await asyncio.shield(request.future)
            except asyncio.CancelledError:
                if not request.started:
                    self._pending.remove(request)
                    request.future.cancel()
                else:
                    # Canceling one camera cannot cancel another camera's batch.
                    # Wait for this GPU job before releasing its frame ownership.
                    while not request.future.done():
                        try:
                            await asyncio.shield(request.future)
                        except asyncio.CancelledError:
                            continue
                        except Exception:  # noqa: BLE001 - preserve cancellation
                            break
                    if not request.future.cancelled():
                        request.future.exception()
                raise

    async def _shutdown(self):
        if self._batch_task is not None:
            await self._await_worker(self._batch_task)

        def release():
            if self._backend is not None:
                self._backend.close()
                self._backend = None

        try:
            job = asyncio.get_running_loop().run_in_executor(self._executor, release)
            await self._await_worker(job)
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True)

    async def close(self):
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._shutdown())
        await self._await_worker(self._close_task)
