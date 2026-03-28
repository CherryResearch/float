from app.base_services import LLMService


class FakeCuda:
    def __init__(self, bf16_supported=True):
        self._bf16_supported = bf16_supported

    def is_available(self):
        return True

    def is_bf16_supported(self):
        return self._bf16_supported


class FakeTorch:
    def __init__(self, bf16_supported=True):
        self.bfloat16 = object()
        self.float16 = object()
        self.cuda = FakeCuda(bf16_supported=bf16_supported)


def test_mxfp4_prefers_bf16_when_supported(monkeypatch):
    fake_torch = FakeTorch(bf16_supported=True)
    monkeypatch.setattr("app.base_services.torch", fake_torch)
    monkeypatch.setattr(
        "app.base_services._mxfp4_kernels_available", lambda: True
    )
    svc = LLMService(config={"local_device_map_strategy": "auto"})
    svc._local_quant_method = "mxfp4"
    model_kwargs = {}
    svc._apply_dtype_preferences(model_kwargs)
    assert model_kwargs["torch_dtype"] is fake_torch.bfloat16


def test_mxfp4_falls_back_to_float16_when_bf16_unavailable(monkeypatch):
    fake_torch = FakeTorch(bf16_supported=False)
    monkeypatch.setattr("app.base_services.torch", fake_torch)
    monkeypatch.setattr(
        "app.base_services._mxfp4_kernels_available", lambda: True
    )
    svc = LLMService(config={"local_device_map_strategy": "auto"})
    svc._local_quant_method = "mxfp4"
    model_kwargs = {}
    svc._apply_dtype_preferences(model_kwargs)
    assert model_kwargs["torch_dtype"] is fake_torch.float16
