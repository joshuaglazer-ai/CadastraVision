"""The real model: U-Net with a ResNet34 encoder, six classes.

Needs PyTorch and segmentation-models-pytorch. The checkpoint test also
needs ``backend/models/best_weighted_multiclass_unet.pth`` (not in git);
it is skipped, not faked, when the file is absent.
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.ai import model as model_module
from backend.ai.model import ModelLoadError

try:
    import segmentation_models_pytorch  # noqa: F401
    import torch

    HAVE_TORCH = True
    TORCH_MISSING = ""
except Exception as exc:  # ImportError or a broken install
    torch = None
    HAVE_TORCH = False
    TORCH_MISSING = f"{type(exc).__name__}: {exc}"

MANIFEST = Path(__file__).parent / "fixtures" / "checkpoint_manifest.json"
CHECKPOINT = Path(model_module.__file__).resolve().parents[1] / "models" / "best_weighted_multiclass_unet.pth"


class ModelStatusTests(unittest.TestCase):
    def test_status_never_claims_a_missing_checkpoint(self):
        status = model_module.model_status(Path("/nonexistent/model.pth"), "scale_255")
        self.assertFalse(status["checkpoint_present"])
        self.assertIsNone(status["checkpoint_bytes"])
        self.assertEqual(status["encoder"], "resnet34")
        self.assertEqual(len(status["classes"]), 6)
        self.assertEqual(status["runtime_available"], HAVE_TORCH)

    def test_manifest_describes_a_six_class_head(self):
        tensors = json.loads(MANIFEST.read_text())["tensors"]
        self.assertEqual(tensors["segmentation_head.0.weight"]["shape"][:2], [6, 16])
        self.assertEqual(tensors["encoder.conv1.weight"]["shape"], [64, 3, 7, 7])


@unittest.skipUnless(HAVE_TORCH, f"PyTorch / segmentation-models-pytorch not usable: {TORCH_MISSING}")
class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model, cls.device = model_module.create_model(torch.device("cpu"))

    def test_architecture_matches_the_trained_checkpoint_layout(self):
        """Every tensor name and shape of the checkpoint exists in the model."""

        manifest = json.loads(MANIFEST.read_text())["tensors"]
        state = self.model.state_dict()
        self.assertEqual(set(state), set(manifest))
        for name, tensor in state.items():
            self.assertEqual(list(tensor.shape), manifest[name]["shape"], name)

    def test_forward_pass_gives_six_class_logits(self):
        self.model.eval()
        with torch.no_grad():
            logits = self.model(torch.zeros(1, 3, 64, 96))
        self.assertEqual(tuple(logits.shape), (1, 6, 64, 96))

    def test_tile_prediction_shapes_and_ranges(self):
        from backend.ai.inference import predict

        image = np.random.default_rng(0).integers(0, 255, size=(70, 100, 3), dtype=np.uint8)
        prediction, probabilities, confidence, entropy = predict(self.model, image, self.device)
        self.assertEqual(prediction.shape, (70, 100))
        self.assertEqual(probabilities.shape, (6, 70, 100))
        np.testing.assert_allclose(probabilities.sum(axis=0), 1.0, atol=1e-4)
        self.assertTrue(((confidence > 0) & (confidence <= 1)).all())
        self.assertTrue(((entropy >= 0) & (entropy <= np.log(6) + 1e-4)).all())
        self.assertLessEqual(int(prediction.max()), 5)

    def test_missing_checkpoint_is_a_clear_error(self):
        with self.assertRaises(ModelLoadError) as caught:
            model_module.load_model(Path("/nonexistent/model.pth"))
        self.assertIn("not found", str(caught.exception))

    def test_incompatible_checkpoint_is_diagnosed_not_loaded(self):
        state = self.model.state_dict()
        wrong = {name: tensor for name, tensor in state.items()}
        wrong["segmentation_head.0.weight"] = torch.zeros(2, 16, 3, 3)  # a two-class head
        wrong["segmentation_head.0.bias"] = torch.zeros(2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "two_class.pth"
            torch.save(wrong, path)
            with self.assertRaises(ModelLoadError) as caught:
                model_module.load_model(path, torch.device("cpu"))
        self.assertIn("shape mismatch", str(caught.exception))

    def test_file_that_is_not_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "junk.pth"
            path.write_bytes(b"this is not a checkpoint")
            with self.assertRaises(ModelLoadError):
                model_module.load_model(path, torch.device("cpu"))

    def test_data_parallel_and_wrapped_checkpoints_load(self):
        wrapped = {"state_dict": {f"module.{k}": v for k, v in self.model.state_dict().items()}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wrapped.pth"
            torch.save(wrapped, path)
            loaded, _ = model_module.load_model(path, torch.device("cpu"))
        self.assertFalse(loaded.training)


@unittest.skipUnless(HAVE_TORCH, f"PyTorch / segmentation-models-pytorch not usable: {TORCH_MISSING}")
@unittest.skipUnless(CHECKPOINT.exists(), "trained checkpoint is not present in backend/models/")
class TrainedCheckpointTests(unittest.TestCase):
    def test_trained_weights_load_strictly_and_predict(self):
        model, device = model_module.load_model(CHECKPOINT, torch.device("cpu"))
        self.assertFalse(model.training)
        with torch.no_grad():
            logits = model(torch.rand(1, 3, 128, 128))
        self.assertEqual(tuple(logits.shape), (1, 6, 128, 128))
        self.assertTrue(torch.isfinite(logits).all())


if __name__ == "__main__":
    unittest.main()
