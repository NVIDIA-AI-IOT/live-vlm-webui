"""Segmentation configuration, masks-only pixels and worker lifecycle without Torch."""

import asyncio
import threading
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest

from live_vlm_webui.segmentation import (
    DEFAULT_CLASSES,
    RETAIL_RECOMMENDED_CONFIDENCE,
    SegmentationConfig,
    SegmentationService,
    SegmentationUnavailable,
    _UltralyticsBackend,
    composite_masks,
)


class FakeBackend:
    def __init__(self, _path):
        self.prepared = []
        self.threads = []
        self.closed = False
        self.calls = 0
        self.block = None
        self.started = None
        self.loop = None

    def status(self):
        self.threads.append(threading.get_ident())

    def prepare(self, config):
        self.threads.append(threading.get_ident())
        self.prepared.append(config.classes)

    def predict(self, image, config):
        self.threads.append(threading.get_ident())
        self.calls += 1
        if self.started:
            self.loop.call_soon_threadsafe(self.started.set)
        if self.block:
            assert self.block.wait(timeout=5)
        mask = np.zeros(image.shape[:2], dtype=bool)
        mask[1:3, 1:3] = True
        return mask, 2

    def close(self):
        self.threads.append(threading.get_ident())
        self.closed = True


def frame_with_pixels():
    image = np.full((6, 8, 3), [20, 40, 60], dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(image, format="bgr24")
    frame.pts = 12345
    frame.time_base = Fraction(1, 90000)
    return frame, image


def test_defaults_are_disabled_and_immutable():
    config = SegmentationConfig.from_dict(None)
    assert not config.enabled
    assert config.classes == DEFAULT_CLASSES
    assert config.imgsz == 640
    assert config.max_det == 50
    assert config.confidence == 0.25
    with pytest.raises(FrozenInstanceError):
        config.enabled = True
    assert SegmentationConfig.from_dict({"opacity": 0.5}).enabled is False


def test_normalizes_explicit_classes_without_mutating_input():
    settings = {"enabled": True, "classes": [" person ", "car"], "imgsz": 960}
    config = SegmentationConfig.from_dict(settings)
    assert config.classes == ("person", "car")
    assert settings["classes"] == [" person ", "car"]
    assert config.to_dict()["classes"] == ["person", "car"]


@pytest.mark.parametrize(
    "settings",
    [
        [],
        True,
        {"unknown": 1},
        {"enabled": 1},
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": -0.1},
        {"opacity": float("inf")},
        {"opacity": 1.1},
        {"imgsz": 640.0},
        {"imgsz": 321},
        {"imgsz": 288},
        {"imgsz": 1312},
        {"max_det": True},
        {"max_det": 0},
        {"max_det": 301},
        {"classes": "person"},
        {"classes": []},
        {"classes": [" "]},
        {"classes": [None]},
        {"classes": ["person", "person "]},
        {"classes": ["x" * 81]},
    ],
)
def test_rejects_invalid_json_settings(settings):
    with pytest.raises(ValueError):
        SegmentationConfig.from_dict(settings)


def test_mask_compositing_preserves_holes_and_unmasked_pixels():
    image = np.full((5, 5, 3), [10, 20, 30], np.uint8)
    mask = np.ones((5, 5), bool)
    mask[2, 2] = False
    output = composite_masks(image, mask, 0.5)
    np.testing.assert_array_equal(output[2, 2], image[2, 2])
    np.testing.assert_array_equal(output[0, 0], [5, 138, 15])
    np.testing.assert_array_equal(image[0, 0], [10, 20, 30])
    with pytest.raises(SegmentationUnavailable, match="original frame size"):
        composite_masks(image, np.zeros((3, 3), bool), 0.5)


@pytest.mark.asyncio
async def test_render_masks_only_keeps_frame_timestamps_and_uses_one_worker():
    backend = FakeBackend(None)
    service = SegmentationService(backend_factory=lambda _: backend)
    config = SegmentationConfig(enabled=True, opacity=1.0)
    frame, original = frame_with_pixels()
    try:
        assert (await service.status())["available"]
        await service.prepare(config)
        result, metrics = await service.render(frame, config)
        pixels = result.to_ndarray(format="bgr24")
        assert result.pts == frame.pts
        assert result.time_base == frame.time_base
        np.testing.assert_array_equal(pixels[0], original[0])
        np.testing.assert_array_equal(pixels[1, 1], [0, 255, 0])
        np.testing.assert_array_equal(pixels[3:], original[3:])
        assert metrics["mask_count"] == 2
        assert metrics["inference_ms"] >= 0
        assert backend.prepared == [DEFAULT_CLASSES]
    finally:
        await service.close()
    assert backend.closed
    assert len(set(backend.threads)) == 1
    assert backend.threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_disabled_segmentation_never_loads_optional_runtime():
    def forbidden(_path):
        raise AssertionError("Disabled segmentation must not load a backend")

    service = SegmentationService(backend_factory=forbidden)
    frame, _ = frame_with_pixels()
    try:
        await service.prepare(SegmentationConfig())
        result, metrics = await service.render(frame, SegmentationConfig())
        assert result is frame
        assert metrics == {"inference_ms": 0.0, "mask_count": 0}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_missing_model_reports_setup_requirement_without_importing_torch(tmp_path):
    service = SegmentationService(tmp_path / "absent.pt")
    try:
        status = await service.status()
        assert not status["available"]
        assert status["device"] == "cuda:0"
        assert "missing" in status["error"]
        assert "setup" in status["error"]
        with pytest.raises(SegmentationUnavailable, match="missing"):
            await service.prepare(SegmentationConfig(enabled=True))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_render_waits_for_non_cancelable_worker_before_finishing():
    backend = FakeBackend(None)
    backend.block = threading.Event()
    backend.started = asyncio.Event()
    backend.loop = asyncio.get_running_loop()
    service = SegmentationService(backend_factory=lambda _: backend)
    frame, _ = frame_with_pixels()
    rendering = asyncio.create_task(service.render(frame, SegmentationConfig(enabled=True)))
    try:
        await asyncio.wait_for(backend.started.wait(), timeout=2)
        rendering.cancel()
        await asyncio.sleep(0.02)
        assert not rendering.done()
        backend.block.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(rendering, timeout=2)
        await service.render(frame, SegmentationConfig(enabled=True))
        assert backend.calls == 2
    finally:
        backend.block.set()
        await asyncio.gather(rendering, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_frame_and_rejects_new_work():
    backend = FakeBackend(None)
    backend.block = threading.Event()
    backend.started = asyncio.Event()
    backend.loop = asyncio.get_running_loop()
    service = SegmentationService(backend_factory=lambda _: backend)
    frame, _ = frame_with_pixels()
    config = SegmentationConfig(enabled=True)
    rendering = asyncio.create_task(service.render(frame, config))
    closing = None
    try:
        await asyncio.wait_for(backend.started.wait(), timeout=2)
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0.02)
        assert not closing.done()
        assert not backend.closed
        with pytest.raises(SegmentationUnavailable, match="closed"):
            await service.render(frame, config)
        backend.block.set()
        await asyncio.wait_for(asyncio.gather(rendering, closing), timeout=2)
        assert backend.closed
        assert not (await service.status())["available"]
        await service.close()
    finally:
        backend.block.set()
        await asyncio.gather(rendering, return_exceptions=True)
        if closing:
            await closing
        await service.close()


def test_prediction_rejects_boxes_without_masks_but_accepts_empty_segmentation():
    class FakeModel:
        def __init__(self):
            self.boxes = [object()]

        def predict(self, **kwargs):
            assert kwargs["retina_masks"] is True
            assert kwargs["device"] == "cuda:0"
            assert "predictor" not in kwargs  # Configurable PyTorch retains its normal predictor.
            assert not kwargs["show"] and not kwargs["save"]
            return [SimpleNamespace(masks=None, boxes=self.boxes)]

    model = FakeModel()
    image = np.zeros((8, 8, 3), np.uint8)
    with pytest.raises(SegmentationUnavailable, match="without segmentation masks"):
        _UltralyticsBackend._predict(model, image, SegmentationConfig(enabled=True))
    model.boxes = []
    mask, count = _UltralyticsBackend._predict(model, image, SegmentationConfig(enabled=True))
    assert count == 0
    assert not mask.any()


def profile_backend(tmp_path):
    """Model/predictor stand-ins expose identity and class activation, without Torch."""
    models = []

    class Embeddings:
        def __init__(self, names, device="cpu", dtype="float32"):
            self.names = tuple(names)
            self.device, self.dtype = device, dtype
            self.transfers = 0

        def to(self, *, device, dtype):
            self.transfers += 1
            return Embeddings(self.names, device, dtype)

    class NativeModel:
        def __init__(self, device):
            self.names = {}
            self.pe = None
            self.parameter = SimpleNamespace(device=device, dtype="float16")
            self.fail_after_update = None

        def parameters(self):
            return iter([self.parameter])

        def set_classes(self, names, embeddings):
            assert tuple(names) == embeddings.names
            self.names = dict(enumerate(names))
            self.pe = embeddings
            if tuple(names) == self.fail_after_update:
                self.fail_after_update = None
                raise RuntimeError("Class switch failed after mutation")

    class Model:
        task = "segment"
        fail_predict_once = False
        fail_setup_once = False

        def __init__(self, _path):
            self.model = NativeModel("cpu")
            self.predictor = None
            self.encoded = []
            self.predictions = []
            self.fail_encode = False
            models.append(self)

        def get_text_pe(self, names):
            if self.fail_encode:
                self.fail_encode = False
                raise RuntimeError("Text encoding failed")
            self.encoded.append(tuple(names))
            return Embeddings(names)

        def set_classes(self, *_args, **_kwargs):
            raise AssertionError("The wrapper setter would discard the live predictor")

        def predict(self, **kwargs):
            if self.predictor is None:
                if self.fail_setup_once:
                    self.fail_setup_once = False
                    self.predictor = SimpleNamespace(model=None)
                    raise RuntimeError("Predictor setup failed")
                native = NativeModel("cuda:0")
                native.set_classes(list(self.model.names.values()), self.model.pe)
                self.predictor = SimpleNamespace(
                    model=SimpleNamespace(
                        format="pt", model=native, backend=SimpleNamespace(names=native.names)
                    )
                )
            if self.fail_predict_once:
                self.fail_predict_once = False
                raise RuntimeError("Warmup failed")
            native = self.predictor.model.model
            assert native.names == self.model.names
            assert self.predictor.model.backend.names == native.names
            self.predictions.append(tuple(native.names.values()))
            return [SimpleNamespace(masks=None, boxes=[]) for _ in kwargs["source"]]

    class Backend(_UltralyticsBackend):
        def status(self):
            self._yoloe = Model

    return Backend(tmp_path / "model.pt"), models


def test_one_model_and_predictor_serve_all_class_profiles_with_bounded_embeddings(tmp_path):
    backend, models = profile_backend(tmp_path)
    profiles = [SegmentationConfig(enabled=True, classes=(f"class-{i}",)) for i in range(5)]
    model = backend.prepare(profiles[0])
    predictor = model.predictor
    weights = predictor.model.model.parameter
    first_embedding = backend._profiles[profiles[0].classes].device_embeddings
    assert first_embedding.device == "cuda:0"
    assert first_embedding.dtype == "float16"
    assert backend.prepare(profiles[0]) is model
    assert model.encoded == [profiles[0].classes]
    assert len(model.predictions) == 1  # One warmup for the shared model.
    for profile in profiles[1:4]:
        assert backend.prepare(profile) is model
    backend.prepare(profiles[0])  # Reuse its existing CPU/CUDA prompt tensors.
    assert backend._profiles[profiles[0].classes].device_embeddings is first_embedding
    assert backend._profiles[profiles[0].classes].embeddings.transfers == 1
    backend.prepare(profiles[4])
    assert len(backend._profiles) == 4
    assert profiles[1].classes not in backend._profiles
    backend.prepare(profiles[1])  # Eviction repeats text encoding, never the checkpoint load.
    assert model.encoded.count(profiles[1].classes) == 2
    assert len(models) == 1
    assert model.predictor is predictor
    assert model.predictor.model.model.parameter is weights
    assert len(model.predictions) == 1
    backend.close()
    assert backend._model is None
    assert not backend._profiles


def test_profile_switching_updates_predictions_and_result_names_without_rebuilding(tmp_path):
    backend, models = profile_backend(tmp_path)
    first = SegmentationConfig(enabled=True, classes=("person", "car"))
    second = replace(first, classes=("bag",))
    images = [np.zeros((8, 8, 3), np.uint8)] * 2
    for config in (first, second, first):
        outputs = backend.predict_many(images, config)
        model = backend._model
        assert len(outputs) == 2
        assert model.predictions[-1] == config.classes
        assert tuple(model.model.names.values()) == config.classes
        assert tuple(model.predictor.model.backend.names.values()) == config.classes
        assert model.predictor.model.model.pe.names == config.classes
    assert len(models) == 1
    assert models[0].encoded == [first.classes, second.classes]


def test_other_settings_do_not_create_models_or_reencode_classes(tmp_path):
    backend, models = profile_backend(tmp_path)
    config = SegmentationConfig(enabled=True)
    model = backend.prepare(config)
    backend.prepare(
        replace(config, confidence=0.8, opacity=0.1, imgsz=416, max_det=10, target_fps=10)
    )
    assert len(models) == 1
    assert model.encoded == [config.classes]
    assert len(model.predictions) == 1


def test_partial_profile_switch_failure_reactivates_previous_classes_before_predicting(tmp_path):
    backend, models = profile_backend(tmp_path)
    first = SegmentationConfig(enabled=True, classes=("person",))
    second = replace(first, classes=("bag",))
    model = backend.prepare(first)
    predictor = model.predictor
    predictor.model.model.fail_after_update = second.classes
    with pytest.raises(RuntimeError, match="after mutation"):
        backend.prepare(second)
    assert backend._active_classes is None
    backend.predict(np.zeros((8, 8, 3), np.uint8), first)
    assert model.predictions[-1] == first.classes
    assert tuple(predictor.model.backend.names.values()) == first.classes
    backend.predict(np.zeros((8, 8, 3), np.uint8), second)
    assert model.predictions[-1] == second.classes
    assert model.predictor is predictor
    assert len(models) == 1


def test_text_encoding_failure_preserves_active_predictor_and_recovers(tmp_path):
    backend, models = profile_backend(tmp_path)
    first = SegmentationConfig(enabled=True, classes=("person",))
    second = replace(first, classes=("bag",))
    model = backend.prepare(first)
    predictor = model.predictor
    model.fail_encode = True
    with pytest.raises(RuntimeError, match="Text encoding failed"):
        backend.prepare(second)
    assert backend._active_classes == first.classes
    assert second.classes not in backend._profiles
    assert tuple(predictor.model.backend.names.values()) == first.classes
    backend.prepare(second)
    assert model.predictor is predictor
    assert len(models) == 1


def test_warmup_failure_retries_without_reloading_checkpoint_or_predictor(tmp_path):
    backend, models = profile_backend(tmp_path)
    backend.status()
    backend._yoloe.fail_predict_once = True
    config = SegmentationConfig(enabled=True)
    with pytest.raises(RuntimeError, match="Warmup failed"):
        backend.prepare(config)
    predictor = models[0].predictor
    assert not backend._warmed
    assert backend._active_classes is None
    backend.prepare(config)
    assert backend._warmed
    assert backend._active_classes == config.classes
    assert models[0].predictor is predictor
    assert len(models) == 1


def test_incomplete_predictor_setup_retries_using_same_checkpoint_and_embeddings(tmp_path):
    backend, models = profile_backend(tmp_path)
    backend.status()
    backend._yoloe.fail_setup_once = True
    config = SegmentationConfig(enabled=True)
    with pytest.raises(RuntimeError, match="Predictor setup failed"):
        backend.prepare(config)
    loaded_model = models[0]
    assert loaded_model.predictor is None
    assert not backend._warmed
    assert backend._active_classes is None
    assert backend.prepare(config) is loaded_model
    assert backend._warmed
    assert backend._active_classes == config.classes
    assert loaded_model.predictor.model is not None
    assert loaded_model.encoded == [config.classes]
    assert len(models) == 1


@pytest.mark.parametrize("value", [True, 0, -1, 61, 25.0, float("nan"), "25"])
def test_rejects_invalid_target_frame_rates(value):
    with pytest.raises(ValueError, match="target_fps"):
        SegmentationConfig.from_dict({"target_fps": value})


@pytest.mark.parametrize("value", [1, 25, 60])
def test_target_frame_rate_is_explicit_and_round_trips(value):
    assert SegmentationConfig().target_fps == 25
    config = SegmentationConfig.from_dict({"target_fps": value})
    assert config.target_fps == value
    assert config.to_dict()["target_fps"] == value


class BatchBackend(FakeBackend):
    def __init__(self, path):
        super().__init__(path)
        self.batches = []
        self.fail = False

    def predict_many(self, images, config):
        self.threads.append(threading.get_ident())
        self.batches.append((len(images), config.classes, [image.shape for image in images]))
        if self.started:
            self.loop.call_soon_threadsafe(self.started.set)
        if self.block:
            assert self.block.wait(timeout=5)
        if self.fail:
            self.fail = False
            raise RuntimeError("batch failed")
        predictions = []
        for image in images:
            mask = np.zeros(image.shape[:2], dtype=bool)
            mask[1:3, 1:3] = True
            predictions.append((mask, int(image[0, 0, 0])))
        return predictions


def distinct_frame(value, *, width=8):
    frame = av.VideoFrame.from_ndarray(np.full((6, width, 3), value, np.uint8), format="bgr24")
    frame.pts = value * 3000
    frame.time_base = Fraction(1, 90000)
    return frame


@pytest.mark.asyncio
async def test_four_camera_batch_preserves_results_timestamps_and_individual_opacity():
    backend = BatchBackend(None)
    service = SegmentationService(backend_factory=lambda _: backend)
    frames = [distinct_frame(i) for i in range(1, 5)]
    configs = [SegmentationConfig(enabled=True, opacity=0 if i == 0 else 1) for i in range(4)]
    try:
        results = await asyncio.gather(
            *(service.render(frame, config) for frame, config in zip(frames, configs))
        )
        assert [batch[0] for batch in backend.batches] == [4]
        for i, (frame, (output, metrics)) in enumerate(zip(frames, results)):
            assert output.pts == frame.pts
            assert output.time_base == frame.time_base
            assert metrics["mask_count"] == i + 1
            assert metrics["batch_size"] == 4
            assert 0 <= metrics["inference_ms"] <= metrics["service_ms"]
            assert 0 <= metrics["queue_ms"] < metrics["total_ms"]
            assert metrics["service_ms"] <= metrics["total_ms"]
            pixels = output.to_ndarray(format="bgr24")
            np.testing.assert_array_equal(pixels[0, 0], [i + 1] * 3)
            np.testing.assert_array_equal(pixels[1, 1], [1, 1, 1] if i == 0 else [0, 255, 0])
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_incompatible_classes_and_geometry_never_share_a_prediction():
    backend = BatchBackend(None)
    service = SegmentationService(backend_factory=lambda _: backend)
    ordinary = SegmentationConfig(enabled=True)
    other_classes = SegmentationConfig(enabled=True, classes=("bag",))
    try:
        results = await asyncio.gather(
            service.render(distinct_frame(1), ordinary),
            service.render(distinct_frame(2), other_classes),
            service.render(distinct_frame(3, width=12), ordinary),
            service.render(distinct_frame(4), ordinary),
        )
        assert [batch[0] for batch in backend.batches] == [2, 1, 1]
        assert backend.batches[0][1] == DEFAULT_CLASSES
        assert backend.batches[1][1] == ("bag",)
        assert backend.batches[2][2] == [(6, 12, 3)]
        assert [metrics["mask_count"] for _, metrics in results] == [1, 2, 3, 4]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_single_camera_does_not_wait_for_a_second_camera():
    backend = BatchBackend(None)
    service = SegmentationService(backend_factory=lambda _: backend)
    try:
        _, metrics = await asyncio.wait_for(
            service.render(distinct_frame(1), SegmentationConfig(enabled=True)), timeout=1
        )
        assert metrics["batch_size"] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_one_inflight_camera_preserves_other_batch_results():
    backend = BatchBackend(None)
    backend.block = threading.Event()
    backend.started = asyncio.Event()
    backend.loop = asyncio.get_running_loop()
    service = SegmentationService(backend_factory=lambda _: backend)
    config = SegmentationConfig(enabled=True)
    tasks = [asyncio.create_task(service.render(distinct_frame(i), config)) for i in (1, 2)]
    try:
        await asyncio.wait_for(backend.started.wait(), timeout=2)
        tasks[0].cancel()
        await asyncio.sleep(0.01)
        tasks[0].cancel()  # Repeated cancellation must still own the shared GPU work.
        await asyncio.sleep(0.01)
        assert not tasks[0].done()
        assert not tasks[1].done()
        backend.block.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)
        assert isinstance(results[0], asyncio.CancelledError)
        assert results[1][1]["mask_count"] == 2
        assert [batch[0] for batch in backend.batches] == [2]
    finally:
        backend.block.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_admission_is_bounded_and_canceled_queued_frames_do_not_reach_gpu():
    backend = BatchBackend(None)
    backend.block = threading.Event()
    backend.started = asyncio.Event()
    backend.loop = asyncio.get_running_loop()
    service = SegmentationService(backend_factory=lambda _: backend)
    config = SegmentationConfig(enabled=True)
    tasks = [asyncio.create_task(service.render(distinct_frame(i), config)) for i in range(1, 22)]
    try:
        await asyncio.wait_for(backend.started.wait(), timeout=2)
        assert len(service._pending) == 12  # Four in flight plus twelve admitted.
        tasks[4].cancel()  # Admitted, but not submitted to the CUDA worker.
        tasks[-1].cancel()  # Still waiting for admission.
        await asyncio.sleep(0.01)
        assert tasks[4].cancelled()
        assert tasks[-1].cancelled()
        assert len(service._pending) == 12
        backend.block.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)
        assert sum(isinstance(result, asyncio.CancelledError) for result in results) == 2
        assert sum(batch[0] for batch in backend.batches) == 19
        assert all(batch[0] <= 4 for batch in backend.batches)
        assert not service._pending
        assert service._batch_task is None
    finally:
        backend.block.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_batch_failure_reaches_all_owners_and_next_batch_can_recover():
    backend = BatchBackend(None)
    backend.fail = True
    service = SegmentationService(backend_factory=lambda _: backend)
    config = SegmentationConfig(enabled=True)
    try:
        results = await asyncio.gather(
            service.render(distinct_frame(1), config),
            service.render(distinct_frame(2), config),
            return_exceptions=True,
        )
        assert all(isinstance(result, SegmentationUnavailable) for result in results)
        assert all("batch failed" in str(result) for result in results)
        _, metrics = await service.render(distinct_frame(3), config)
        assert metrics["mask_count"] == 3
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_close_during_collection_drains_accepted_frames_before_releasing_backend():
    backend = BatchBackend(None)
    service = SegmentationService(backend_factory=lambda _: backend)
    rendering = asyncio.create_task(
        service.render(distinct_frame(1), SegmentationConfig(enabled=True))
    )
    await asyncio.sleep(0)
    await service.close()
    _, metrics = await rendering
    assert metrics["mask_count"] == 1
    assert backend.closed
    assert service._batch_task is None


def test_batch_prediction_validates_result_count():
    class IncorrectModel:
        def predict(self, **_kwargs):
            return []

    with pytest.raises(SegmentationUnavailable, match="number of frame results"):
        _UltralyticsBackend._predict_many(
            IncorrectModel(), [np.zeros((8, 8, 3), np.uint8)], SegmentationConfig(enabled=True)
        )


@pytest.mark.parametrize("opacity", [0, 0.1, 0.45, 0.5, 0.999, 1])
def test_vectorized_compositor_matches_original_float32_pixels_exactly(opacity):
    rng = np.random.default_rng(23)
    image = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    original = image.copy()
    mask = rng.random(image.shape[:2]) < 0.4
    expected = image.copy()
    expected[mask] = np.clip(
        np.rint(
            image[mask].astype(np.float32) * (1 - opacity)
            + np.array([0, 255, 0], np.float32) * opacity
        ),
        0,
        255,
    ).astype(np.uint8)
    np.testing.assert_array_equal(composite_masks(image, mask, opacity), expected)
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("shape", [(48, 64), (16, 80), (80, 16)])
@pytest.mark.parametrize("kind", ["empty", "full", "sparse", "interior_with_hole"])
def test_roi_compositor_keeps_exact_pixels_for_empty_sparse_and_portrait_frames(shape, kind):
    rng = np.random.default_rng(27)
    image = rng.integers(0, 256, (*shape, 3), dtype=np.uint8)
    original = image.copy()
    mask = np.zeros(shape, dtype=bool)
    if kind == "full":
        mask[:] = True
    elif kind == "sparse":
        mask[:] = rng.random(shape) < 0.005
    elif kind == "interior_with_hole":
        mask[3:12, 4:13] = True
        mask[6:8, 7:9] = False
    for opacity in (0, 0.1, 0.45, 1):
        expected = image.copy()
        expected[mask] = np.clip(
            np.rint(
                image[mask].astype(np.float32) * (1 - opacity)
                + np.array([0, 255, 0], np.float32) * opacity
            ),
            0,
            255,
        ).astype(np.uint8)
        np.testing.assert_array_equal(composite_masks(image, mask, opacity), expected)
        np.testing.assert_array_equal(image, original)


class MaskTensor:
    """Count host synchronizations without requiring a Torch installation."""

    copies = 0

    def __init__(self, values):
        self.values = np.asarray(values)
        self.ndim = self.values.ndim
        self.shape = self.values.shape

    def __len__(self):
        return len(self.values)

    def amax(self, dim):
        return MaskTensor(self.values.max(axis=dim))

    def bool(self):
        return MaskTensor(self.values.astype(bool))

    def new_zeros(self, shape):
        return MaskTensor(np.zeros(shape, dtype=bool))

    def cpu(self):
        MaskTensor.copies += 1
        return self

    def numpy(self):
        return self.values


def mask_result(values=None, *, boxes=()):
    return SimpleNamespace(
        masks=None if values is None else SimpleNamespace(data=MaskTensor(values)), boxes=boxes
    )


def test_batched_mask_transfer_copies_once_and_preserves_empty_positions(monkeypatch):
    from live_vlm_webui import segmentation

    fake_torch = SimpleNamespace(
        stack=lambda masks: MaskTensor(np.stack([m.values for m in masks]))
    )
    monkeypatch.setattr(segmentation.importlib, "import_module", lambda name: fake_torch)
    shape = (8, 12)
    first = np.zeros((2, *shape), dtype=bool)
    first[0, 1:3, 2:5] = True
    first[1, 2:5, 4:7] = True
    second = np.zeros((1, *shape), dtype=bool)
    second[0, 6, 8] = True
    results = [
        mask_result(),
        mask_result(first),
        mask_result(np.empty((0, *shape))),
        mask_result(second),
    ]
    MaskTensor.copies = 0
    pairs = _UltralyticsBackend._mask_results(results, [np.zeros((*shape, 3))] * 4)
    assert MaskTensor.copies == 1
    assert [count for _, count in pairs] == [0, 2, 0, 1]
    assert not pairs[0][0].any() and not pairs[2][0].any()
    np.testing.assert_array_equal(pairs[1][0], first.any(axis=0))
    np.testing.assert_array_equal(pairs[3][0], second.any(axis=0))


def test_all_empty_masks_skip_torch_and_keep_independent_cpu_outputs(monkeypatch):
    from live_vlm_webui import segmentation

    def forbidden(_name):
        raise AssertionError("All-empty masks require no Torch or device transfer")

    monkeypatch.setattr(segmentation.importlib, "import_module", forbidden)
    images = [np.zeros((8, 12, 3))] * 2
    pairs = _UltralyticsBackend._mask_results(
        [mask_result(), mask_result(np.empty((0, 8, 12)))], images
    )
    assert [count for _, count in pairs] == [0, 0]
    pairs[0][0][0, 0] = True
    assert not pairs[1][0].any()


@pytest.mark.parametrize(
    "bad_result", [mask_result(boxes=[object()]), mask_result(np.zeros((1, 4, 4)))]
)
def test_batch_validates_every_mask_before_transfer(bad_result):
    with pytest.raises(SegmentationUnavailable):
        _UltralyticsBackend._mask_results([mask_result(), bad_result], [np.zeros((8, 12, 3))] * 2)


def write_engine_metadata(path, **overrides):
    import json
    import struct

    from live_vlm_webui.segmentation import RETAIL_CLASSES

    metadata = {
        "task": "segment",
        "names": dict(enumerate(RETAIL_CLASSES)),
        "imgsz": [288, 480],
        "batch": 4,
    }
    metadata.update(overrides)
    encoded = json.dumps(metadata).encode()
    path.write_bytes(struct.pack("<I", len(encoded)) + encoded + b"fake-engine-payload")
    return path


def engine_runtime(monkeypatch, tmp_path):
    from live_vlm_webui import segmentation

    models, imports = [], []

    class Engine:
        num_io_tensors = 3
        num_optimization_profiles = 1
        shapes = ((1, 3, 288, 480), (4, 3, 288, 480), (4, 3, 288, 480))

        def get_tensor_name(self, index):
            return ("images", "output0", "output1")[index]

        def get_tensor_mode(self, name):
            return "input" if name == "images" else "output"

        def get_tensor_profile_shape(self, name, profile):
            assert name == "images" and profile == 0
            return self.shapes

    class Model:
        fail_setup_once = False

        def __init__(self, path, task):
            assert task == "segment" and path.endswith(".engine")
            self.predictor = None
            self.calls = []
            models.append(self)

        def predict(self, **kwargs):
            self.calls.append(kwargs)
            if self.predictor is None:
                if self.fail_setup_once:
                    self.fail_setup_once = False
                    self.predictor = SimpleNamespace(model=None)
                    raise RuntimeError("Engine setup failed")
                self.predictor = SimpleNamespace(
                    model=SimpleNamespace(format="engine", model=Engine())
                )
            return [mask_result() for _ in kwargs["source"]]

    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    trt = SimpleNamespace(TensorIOMode=SimpleNamespace(INPUT="input"))
    ultralytics = SimpleNamespace(YOLO=Model)
    predictor_module = SimpleNamespace(SegmentationPredictor=type("SegmentationPredictor", (), {}))
    modules = {
        "torch": torch,
        "tensorrt": trt,
        "ultralytics": ultralytics,
        "ultralytics.models.yolo.segment.predict": predictor_module,
    }
    original_import = segmentation.importlib.import_module

    def import_module(name, *args):
        imports.append(name)
        if name == "clip":
            raise AssertionError("A fixed engine must not import a text encoder")
        return modules[name] if name in modules else original_import(name, *args)

    monkeypatch.setattr(segmentation.importlib, "import_module", import_module)
    path = write_engine_metadata(tmp_path / "retail.engine")
    return SimpleNamespace(
        path=path, models=models, imports=imports, model=Model, engine=Engine, torch=torch
    )


@pytest.mark.asyncio
async def test_engine_status_is_lazy_has_fixed_constraints_and_needs_no_encoder(
    monkeypatch, tmp_path
):
    from live_vlm_webui.segmentation import RETAIL_CLASSES, _TensorRTRetailBackend

    runtime = engine_runtime(monkeypatch, tmp_path)
    service = SegmentationService(runtime.path)
    try:
        assert runtime.imports == []
        assert runtime.models == []
        status = await service.status()
        assert status["available"]
        assert status["backend"] == "tensorrt"
        assert status["fixed_classes"] == list(RETAIL_CLASSES)
        assert status["fixed_imgsz"] == 480
        assert status["recommended_confidence"] == RETAIL_RECOMMENDED_CONFIDENCE == 0.05
        assert isinstance(service._backend, _TensorRTRetailBackend)
        assert runtime.models == []  # Status imports/checks assets, never loads GPU weights.
        assert "clip" not in runtime.imports
        assert not (tmp_path / "mobileclip2_b.ts").exists()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_default_pytorch_status_has_no_fixed_profile(tmp_path):
    service = SegmentationService(tmp_path / "absent.pt")
    try:
        status = await service.status()
        assert status["backend"] == "pytorch"
        assert status["fixed_classes"] is None
        assert status["fixed_imgsz"] is None
        assert status["recommended_confidence"] == SegmentationConfig().confidence == 0.25
    finally:
        await service.close()


@pytest.mark.parametrize(
    "override",
    [
        {"task": "detect"},
        {"names": {0: "person"}},
        {"imgsz": [480, 480]},
        {"batch": 1},
        {"batch": "4"},
    ],
)
def test_engine_metadata_rejected_before_optional_runtime_import(monkeypatch, tmp_path, override):
    from live_vlm_webui import segmentation

    path = write_engine_metadata(tmp_path / "bad.engine", **override)

    def forbidden(_name):
        raise AssertionError("Bad metadata must be rejected before importing CUDA")

    monkeypatch.setattr(segmentation.importlib, "import_module", forbidden)
    with pytest.raises(SegmentationUnavailable, match="metadata"):
        segmentation._TensorRTRetailBackend(path).status()


@pytest.mark.parametrize("content", [b"", b"bad", b"\xff\xff\xff\xffbad", b"\x02\x00\x00\x00{}"])
def test_engine_missing_or_truncated_metadata_reports_readable_setup_error(tmp_path, content):
    from live_vlm_webui.segmentation import _TensorRTRetailBackend

    path = tmp_path / "bad.engine"
    path.write_bytes(content)
    with pytest.raises(SegmentationUnavailable, match="metadata"):
        _TensorRTRetailBackend(path).status()


def test_engine_reuses_one_predictor_warms_all_batches_and_preserves_inference_options(
    monkeypatch, tmp_path
):
    from live_vlm_webui.segmentation import RETAIL_CLASSES, _TensorRTRetailBackend

    runtime = engine_runtime(monkeypatch, tmp_path)
    backend = _TensorRTRetailBackend(runtime.path)
    config = SegmentationConfig(enabled=True, classes=RETAIL_CLASSES, imgsz=480)
    model = backend.prepare(config)
    predictor = model.predictor
    assert [len(call["source"]) for call in model.calls] == [1, 2, 3, 4]
    assert all(image.shape == (288, 480, 3) for call in model.calls for image in call["source"])
    changed = replace(config, confidence=0.7, opacity=0.8, max_det=9, target_fps=10)
    images = [np.zeros((73, 29, 3), dtype=np.uint8), np.zeros((60, 80, 3), dtype=np.uint8)]
    pairs = backend.predict_many(images, changed)
    assert [mask.shape for mask, _ in pairs] == [(73, 29), (60, 80)]
    assert model.predictor is predictor
    assert len(runtime.models) == 1
    assert len(model.calls) == 5
    call = model.calls[-1]
    assert call["imgsz"] == [288, 480] and call["rect"] is False
    assert all(call["agnostic_nms"] is True for call in model.calls)
    assert all(call["predictor"] is backend._predictor_type for call in model.calls)
    assert backend._predictor_type.__name__ == "CPUPostprocessPredictor"
    assert call["device"] == "cuda:0"
    assert call["retina_masks"] is True
    assert call["conf"] == 0.7 and call["max_det"] == 9
    backend.close()
    assert backend._model is None


@pytest.mark.parametrize("override", [{"classes": ("person",)}, {"imgsz": 640}])
def test_engine_rejects_custom_settings_before_loading_model(monkeypatch, tmp_path, override):
    from live_vlm_webui.segmentation import RETAIL_CLASSES, _TensorRTRetailBackend

    runtime = engine_runtime(monkeypatch, tmp_path)
    config = replace(
        SegmentationConfig(enabled=True, classes=RETAIL_CLASSES, imgsz=480), **override
    )
    with pytest.raises(SegmentationUnavailable, match="Use the PyTorch model"):
        _TensorRTRetailBackend(runtime.path).prepare(config)
    assert runtime.imports == []
    assert runtime.models == []


def test_engine_rejects_actual_profile_mismatch_even_if_metadata_claims_supported(
    monkeypatch, tmp_path
):
    from live_vlm_webui.segmentation import RETAIL_CLASSES, _TensorRTRetailBackend

    runtime = engine_runtime(monkeypatch, tmp_path)
    runtime.engine.shapes = ((1, 3, 480, 480), (4, 3, 480, 480), (4, 3, 480, 480))
    backend = _TensorRTRetailBackend(runtime.path)
    with pytest.raises(SegmentationUnavailable, match="fixed 288x480"):
        backend.prepare(SegmentationConfig(enabled=True, classes=RETAIL_CLASSES, imgsz=480))
    assert not backend._warmed


def test_engine_setup_failure_retries_same_model_wrapper(monkeypatch, tmp_path):
    from live_vlm_webui.segmentation import RETAIL_CLASSES, _TensorRTRetailBackend

    runtime = engine_runtime(monkeypatch, tmp_path)
    runtime.model.fail_setup_once = True
    backend = _TensorRTRetailBackend(runtime.path)
    config = SegmentationConfig(enabled=True, classes=RETAIL_CLASSES, imgsz=480)
    with pytest.raises(RuntimeError, match="Engine setup failed"):
        backend.prepare(config)
    model = runtime.models[0]
    assert model.predictor is None
    assert backend.prepare(config) is model
    assert len(runtime.models) == 1
    assert backend._warmed


@pytest.mark.parametrize("empty", [False, True])
def test_engine_cpu_postprocess_preserves_inputs_dtype_results_and_empty_masks(empty):
    from live_vlm_webui.segmentation import _cpu_postprocess_predictor

    copies = []

    class Tensor:
        def __init__(self, values, *, device="cuda:0", detached=False):
            self.values = np.asarray(values)
            self.device, self.detached = device, detached
            self.dtype = self.values.dtype

        def __len__(self):
            return len(self.values)

        def detach(self):
            return Tensor(self.values, device=self.device, detached=True)

        def cpu(self):
            assert self.detached
            copies.append(self)
            return Tensor(self.values, device="cpu", detached=True)

    boxes = Tensor(np.empty((0 if empty else 2, 6), dtype=np.float32))
    prototypes = Tensor(np.zeros((4, 8, 12), dtype=np.float16))
    auxiliary = Tensor(np.ones((2,), dtype=np.float32))
    metadata = object()
    predictions = [boxes, {"protos": (prototypes,), "aux": [auxiliary, None], "metadata": metadata}]
    # The preprocessed network input must remain on CUDA; postprocess uses its shape.
    network_image = Tensor(np.zeros((1, 3, 288, 480), dtype=np.float32))
    original = np.zeros((73, 29, 3), dtype=np.uint8)
    originals = [original]
    names = {0: "person", 1: "shopping cart", 2: "shopping basket", 3: "backpack"}
    calls = []

    class SegmentationPredictor:
        def __init__(self):
            self.model = SimpleNamespace(names=names)
            self.batch = (["camera-frame"], originals, None)

        def postprocess(self, preds, img, orig_imgs):
            calls.append((preds, img, orig_imgs))
            assert img is network_image and img.device == "cuda:0"
            assert orig_imgs is originals and orig_imgs[0] is original
            assert isinstance(preds, list) and isinstance(preds[1], dict)
            assert isinstance(preds[1]["protos"], tuple)
            assert isinstance(preds[1]["aux"], list)
            assert preds[1]["aux"][1] is None
            assert preds[1]["metadata"] is metadata
            assert preds[0].device == preds[1]["protos"][0].device == "cpu"
            assert preds[1]["aux"][0].device == "cpu"
            assert preds[0].dtype == np.float32
            assert preds[1]["protos"][0].dtype == np.float16
            np.testing.assert_array_equal(preds[0].values, boxes.values)
            np.testing.assert_array_equal(preds[1]["protos"][0].values, prototypes.values)
            return [
                SimpleNamespace(
                    boxes=preds[0],
                    masks=None if empty else preds[1]["protos"][0],
                    names=self.model.names,
                    orig_img=orig_imgs[0],
                    path=self.batch[0][0],
                )
            ]

    predictor_type = _cpu_postprocess_predictor(
        SimpleNamespace(Tensor=Tensor), SegmentationPredictor
    )
    predictor = predictor_type()
    results = predictor.postprocess(predictions, network_image, originals)
    assert len(calls) == 1 and len(copies) == 3
    assert boxes.device == prototypes.device == auxiliary.device == network_image.device == "cuda:0"
    assert not boxes.detached  # Input ownership and state remain unchanged.
    assert results[0].names is names
    assert results[0].orig_img is original
    assert results[0].path == "camera-frame"
    assert results[0].boxes.device == "cpu"
    if empty:
        mask, count = _UltralyticsBackend._mask_results(results, originals)[0]
        assert count == 0 and mask.shape == original.shape[:2] and not mask.any()
    else:
        assert results[0].masks.device == "cpu"
