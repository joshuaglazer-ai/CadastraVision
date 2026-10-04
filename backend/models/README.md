# Model weights

Checkpoints live in this folder and are never committed (`*.pth` and `*.pt` are
ignored by git). `registry.json` lists the ones a processing job can use:

| Id | File to place here | Notes |
| --- | --- | --- |
| `village` (default) | `best_weighted_multiclass_unet.pth` | SVAMITVA village model, about 98 MB |
| `urban` | `cadastra_unet_resnet34_uavpal_sep.pth` | Fine-tuned on UAVPal drone imagery of Bhopal, trained to keep touching buildings apart. CC BY-NC-SA 4.0: research and demo use |

Every checkpoint must be a PyTorch `state_dict` for `segmentation_models_pytorch.Unet`
with a ResNet34 encoder, 3 input channels and 6 output classes (278 tensors), trained
on 8-bit RGB divided by 255. A file that is not present is shown as unavailable on the
Processing page; nothing else changes.

Check a checkpoint without PyTorch (the default one, or any file you name):

    python -m backend.scripts.inspect_checkpoint
    python -m backend.scripts.inspect_checkpoint backend/models/cadastra_unet_resnet34_uavpal_sep.pth
