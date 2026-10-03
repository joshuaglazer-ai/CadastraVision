# Model weights

Place the trained checkpoint here:

    backend/models/best_weighted_multiclass_unet.pth

It is a PyTorch `state_dict` for `segmentation_models_pytorch.Unet` with a
ResNet34 encoder, 3 input channels and 6 output classes (278 tensors,
about 98 MB). The file is not stored in git.

Check a checkpoint without PyTorch:

    python -m backend.scripts.inspect_checkpoint
